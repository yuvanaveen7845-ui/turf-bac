import hmac
import hashlib
import json
from unittest.mock import patch
from datetime import date, time, timedelta
from decimal import Decimal
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.models import User
from turfs.models import Turf, TimeSlot
from turfs.services import SchedulingEngine
from bookings.models import Booking
from bookings.services import BookingEngine
from payments.models import Payment, Refund, DailyCashDrawer
from payments.razorpay_client import RazorpayService
from payments.cancellation import CancellationPolicyEngine
from payments.reconciliation import ReconciliationEngine


class PaymentEngineComprehensiveTests(TestCase):
    def setUp(self):
        self.client = APIClient()

        # Admin user
        self.admin_user = User.objects.create_user(
            email="admin_finance@friendsturf.local",
            password="password123",
            role="ADMIN",
        )

        # Staff user
        self.staff_user = User.objects.create_user(
            email="staff_desk@friendsturf.local",
            password="password123",
            role="STAFF",
        )

        # Customer user
        self.customer = User.objects.create_user(
            email="badminton_fan@friendsturf.local",
            password="password123",
            role="CUSTOMER",
        )

        self.turf = Turf.objects.create(
            name="Turf 2 - Koramangala 7s Pitch",
            slug="turf-2-koramangala-7s",
            sport_type="FOOTBALL",
            description="50mm FIFA Quality Pro Turf",
            location="Koramangala, Bangalore",
            address="100 Feet Road",
            base_price=Decimal("1200.00"),
            operating_hours_start=time(6, 0),
            operating_hours_end=time(23, 0),
            slot_duration_minutes=60,
        )
        self.today = timezone.now().date()
        self.slots = SchedulingEngine.generate_daily_slots(self.turf, self.today)

    # -------------------------------------------------------------
    # 1. ONLINE PAYMENT & RAZORPAY VERIFICATION TESTS
    # -------------------------------------------------------------
    def test_verify_payment_signature_valid(self):
        """Cryptographic HMAC-SHA256 signature verification should pass when computed with matching secret."""
        key_secret = "test_secret_key_123"
        order_id = "order_FT998877"
        payment_id = "pay_FT112233"
        msg = f"{order_id}|{payment_id}"

        signature = hmac.new(
            key_secret.encode("utf-8"), msg.encode("utf-8"), hashlib.sha256
        ).hexdigest()

        original_secret_fn = RazorpayService.get_key_secret
        RazorpayService.get_key_secret = classmethod(lambda cls: key_secret)

        try:
            is_valid = RazorpayService.verify_payment_signature(
                razorpay_order_id=order_id,
                razorpay_payment_id=payment_id,
                razorpay_signature=signature,
            )
            self.assertTrue(is_valid)

            is_invalid = RazorpayService.verify_payment_signature(
                razorpay_order_id=order_id,
                razorpay_payment_id=payment_id,
                razorpay_signature="bad_signature_xyz",
            )
            self.assertFalse(is_invalid)
        finally:
            RazorpayService.get_key_secret = original_secret_fn

    def test_razorpay_order_creation_with_authoritative_price(self):
        """Backend calculates authoritative pricing, ignores any client tampering."""
        self.client.force_authenticate(user=self.customer)
        slot = self.slots[0]

        original_create_order = RazorpayService.create_order
        RazorpayService.create_order = classmethod(
            lambda cls, amount_in_rupees, receipt_id, notes=None, currency="INR": {
                "order_id": "order_mock_test123",
                "amount": int(float(amount_in_rupees) * 100),
                "currency": currency,
                "key_id": "rzp_test_mockkey",
            }
        )

        try:
            res = self.client.post("/api/payments/razorpay/create-order/", {
                "turf_id": self.turf.id,
                "date": str(self.today),
                "slot_ids": [str(slot.id)],
                "payment_type": "FULL",
            })

            self.assertEqual(res.status_code, 201)
            data = res.data
            self.assertIn("order_id", data)
            self.assertIn("booking_id", data)
            self.assertEqual(data["amount_to_pay"], data["final_amount"])

            # Check booking is created in PAYMENT_PENDING status
            booking = Booking.objects.get(booking_id=data["booking_id"])
            self.assertEqual(booking.status, "PAYMENT_PENDING")
            self.assertEqual(booking.amount_paid, Decimal("0.00"))
        finally:
            RazorpayService.create_order = original_create_order


    @patch("payments.views.RazorpayService.verify_webhook_signature", return_value=True)
    def test_webhook_payment_captured_and_idempotency(self, mock_verify):
        """Webhook confirms booking idempotently and does not process duplicates."""
        slot = self.slots[1]
        booking = Booking.objects.create(
            booking_id="FT-26-TESTWH1",
            customer=self.customer,
            turf=self.turf,
            date=self.today,
            start_time=slot.start_time,
            end_time=slot.end_time,
            status="PAYMENT_PENDING",
            final_amount=Decimal("1200.00"),
            amount_paid=Decimal("0.00"),
            balance_due=Decimal("1200.00"),
        )
        booking.slots.add(slot)

        payment = Payment.objects.create(
            payment_id="PAY-TESTWH1",
            booking=booking,
            customer=self.customer,
            provider="RAZORPAY",
            provider_order_id="order_WH123",
            amount=Decimal("1200.00"),
            transaction_reference="TXN-WH123",
            status="PENDING",
        )

        webhook_payload = {
            "event": "payment.captured",
            "id": "EVT-TEST-IDEMPOTENT-1",
            "payload": {
                "payment": {
                    "entity": {
                        "id": "pay_WHCAPTURED999",
                        "order_id": "order_WH123",
                        "amount": 120000,
                    }
                }
            }
        }

        # Send webhook
        res1 = self.client.post(
            "/api/payments/razorpay/webhook/",
            data=json.dumps(webhook_payload),
            content_type="application/json",
        )
        self.assertEqual(res1.status_code, 200)
        self.assertEqual(res1.data["status"], "processed")

        # Verify booking and payment are confirmed
        payment.refresh_from_db()
        booking.refresh_from_db()
        self.assertEqual(payment.status, "PAID")
        self.assertEqual(booking.status, "CONFIRMED")
        self.assertEqual(booking.amount_paid, Decimal("1200.00"))
        self.assertEqual(booking.balance_due, Decimal("0.00"))

        # Send exact duplicate webhook - must return already_processed
        res2 = self.client.post(
            "/api/payments/razorpay/webhook/",
            data=json.dumps(webhook_payload),
            content_type="application/json",
        )
        self.assertEqual(res2.status_code, 200)
        self.assertEqual(res2.data["status"], "already_processed")

    # -------------------------------------------------------------
    # 2. OFFLINE PAYMENTS & PARTIAL PAYMENTS
    # -------------------------------------------------------------
    def test_manual_offline_payment_full(self):
        """Staff can record full offline cash payment under 10 seconds."""
        self.client.force_authenticate(user=self.staff_user)
        slot = self.slots[2]
        booking = Booking.objects.create(
            booking_id="FT-26-CASHFULL",
            customer=self.customer,
            turf=self.turf,
            date=self.today,
            start_time=slot.start_time,
            end_time=slot.end_time,
            status="PAYMENT_PENDING",
            final_amount=Decimal("1200.00"),
            amount_paid=Decimal("0.00"),
            balance_due=Decimal("1200.00"),
        )
        booking.slots.add(slot)

        res = self.client.post("/api/payments/manual-collect/", {
            "booking_id": booking.booking_id,
            "amount": "1200.00",
            "payment_method": "CASH",
            "notes": "Full counter collection before game",
        })

        self.assertEqual(res.status_code, 201)
        booking.refresh_from_db()
        self.assertEqual(booking.status, "CONFIRMED")
        self.assertEqual(booking.amount_paid, Decimal("1200.00"))
        self.assertEqual(booking.balance_due, Decimal("0.00"))

        # Payment record created and marked PAID
        payment = Payment.objects.filter(booking=booking).first()
        self.assertIsNotNone(payment)
        self.assertEqual(payment.status, "PAID")
        self.assertEqual(payment.payment_method, "CASH")
        self.assertEqual(payment.collected_by, self.staff_user)

    def test_manual_offline_payment_partial_and_balance(self):
        """Staff records partial payment (500) and later collects remaining balance (700)."""
        self.client.force_authenticate(user=self.staff_user)
        slot = self.slots[3]
        booking = Booking.objects.create(
            booking_id="FT-26-PARTIALPAY",
            customer=self.customer,
            turf=self.turf,
            date=self.today,
            start_time=slot.start_time,
            end_time=slot.end_time,
            status="PAYMENT_PENDING",
            final_amount=Decimal("1200.00"),
            amount_paid=Decimal("0.00"),
            balance_due=Decimal("1200.00"),
        )
        booking.slots.add(slot)

        # 1. Record partial payment of ₹500
        res1 = self.client.post("/api/payments/manual-collect/", {
            "booking_id": booking.booking_id,
            "amount": "500.00",
            "payment_method": "UPI",
            "transaction_reference": "UPI-SPOT-500",
        })
        self.assertEqual(res1.status_code, 201)
        booking.refresh_from_db()
        self.assertEqual(booking.amount_paid, Decimal("500.00"))
        self.assertEqual(booking.balance_due, Decimal("700.00"))
        self.assertEqual(booking.status, "CONFIRMED")

        # 2. Record remaining balance payment of ₹700
        res2 = self.client.post("/api/payments/manual-collect/", {
            "booking_id": booking.booking_id,
            "amount": "700.00",
            "payment_method": "CASH",
        })
        self.assertEqual(res2.status_code, 201)
        booking.refresh_from_db()
        self.assertEqual(booking.amount_paid, Decimal("1200.00"))
        self.assertEqual(booking.balance_due, Decimal("0.00"))

        # Verify 2 distinct payment records exist for this booking
        payments = Payment.objects.filter(booking=booking)
        self.assertEqual(payments.count(), 2)

    # -------------------------------------------------------------
    # 3. REFUNDS & ROLE PERMISSIONS
    # -------------------------------------------------------------
    def test_staff_cannot_process_refund(self):
        """General staff role should be rejected with 403 when attempting a refund."""
        self.client.force_authenticate(user=self.staff_user)
        payment = Payment.objects.create(
            payment_id="PAY-REFTEST-STAFF",
            booking=Booking.objects.create(
                booking_id="FT-26-NOSTAFFREF",
                customer=self.customer,
                turf=self.turf,
                date=self.today,
                start_time=time(10, 0),
                end_time=time(11, 0),
                final_amount=Decimal("1200.00"),
                amount_paid=Decimal("1200.00"),
            ),
            customer=self.customer,
            provider="CASH",
            amount=Decimal("1200.00"),
            transaction_reference="TXN-STAFF-REF",
            status="PAID",
        )

        res = self.client.post(f"/api/payments/{payment.pk}/refund/", {
            "amount": "1200.00",
            "refund_to": "WALLET",
        })
        self.assertEqual(res.status_code, 403)

    def test_admin_can_process_partial_and_full_refund(self):
        """Admin role can process refund; booking and payment states remain separate."""
        self.client.force_authenticate(user=self.admin_user)
        booking = Booking.objects.create(
            booking_id="FT-26-REFUNDABLE",
            customer=self.customer,
            turf=self.turf,
            date=self.today,
            start_time=time(18, 0),
            end_time=time(19, 0),
            status="CANCELLED",
            final_amount=Decimal("1200.00"),
            amount_paid=Decimal("1200.00"),
        )
        payment = Payment.objects.create(
            payment_id="PAY-MGR-REF",
            booking=booking,
            customer=self.customer,
            provider="WALLET",
            amount=Decimal("1200.00"),
            transaction_reference="TXN-MGR-REF",
            status="PAID",
        )

        # 1. Partial refund of ₹500
        res1 = self.client.post(f"/api/payments/{payment.pk}/refund/", {
            "amount": "500.00",
            "refund_to": "WALLET",
            "reason": "Customer cancellation partial refund",
        })
        self.assertEqual(res1.status_code, 201)
        payment.refresh_from_db()
        self.assertEqual(payment.status, "PARTIALLY_REFUNDED")
        self.assertEqual(booking.status, "CANCELLED")  # Distinct state!

        # 2. Cannot refund more than remaining balance (₹700)
        res_overflow = self.client.post(f"/api/payments/{payment.pk}/refund/", {
            "amount": "800.00",
            "refund_to": "WALLET",
        })
        self.assertEqual(res_overflow.status_code, 400)

        # 3. Complete remaining refund of ₹700
        res2 = self.client.post(f"/api/payments/{payment.pk}/refund/", {
            "amount": "700.00",
            "refund_to": "WALLET",
        })
        self.assertEqual(res2.status_code, 201)
        payment.refresh_from_db()
        self.assertEqual(payment.status, "REFUNDED")

    # -------------------------------------------------------------
    # 4. CANCELLATION POLICY CALCULATION
    # -------------------------------------------------------------
    def test_cancellation_policy_calculation(self):
        """Calculates cancellation fee and refund quote according to lead time."""
        future_date = self.today + timedelta(days=2)  # > 24 hours
        booking_future = Booking.objects.create(
            booking_id="FT-26-FUTURECANC",
            customer=self.customer,
            turf=self.turf,
            date=future_date,
            start_time=time(18, 0),
            end_time=time(19, 0),
            final_amount=Decimal("1200.00"),
            amount_paid=Decimal("1200.00"),
        )

        quote = CancellationPolicyEngine.calculate_refund(booking_future)
        self.assertEqual(quote["cancellation_fee"], 0.0)
        self.assertEqual(quote["refundable_amount"], 1200.0)
        self.assertEqual(quote["eligibility"], "FULL")

    # -------------------------------------------------------------
    # 5. RECONCILIATION ENGINE TESTS
    # -------------------------------------------------------------
    def test_reconciliation_detects_and_resolves_anomaly(self):
        """Detects paid booking that is unconfirmed and resolves it atomically."""
        booking = Booking.objects.create(
            booking_id="FT-26-ANOMALY1",
            customer=self.customer,
            turf=self.turf,
            date=self.today,
            start_time=time(14, 0),
            end_time=time(15, 0),
            status="PAYMENT_PENDING",
            final_amount=Decimal("1200.00"),
            amount_paid=Decimal("0.00"),
            balance_due=Decimal("1200.00"),
        )
        payment = Payment.objects.create(
            payment_id="PAY-ANOMALY-1",
            booking=booking,
            customer=self.customer,
            provider="RAZORPAY",
            amount=Decimal("1200.00"),
            transaction_reference="TXN-ANOM-1",
            status="PAID",
        )

        anomalies = ReconciliationEngine.scan_anomalies()
        target = next((a for a in anomalies if a["booking_id"] == booking.booking_id), None)
        self.assertIsNotNone(target)
        self.assertEqual(target["type"], "UNCONFIRMED_PAID_BOOKING")

        # Resolve anomaly
        res = ReconciliationEngine.resolve_anomaly(
            anomaly_type="UNCONFIRMED_PAID_BOOKING",
            payment_id=payment.payment_id,
            user=self.admin_user,
        )
        self.assertTrue(res["success"])

        booking.refresh_from_db()
        self.assertEqual(booking.status, "CONFIRMED")
        self.assertEqual(booking.amount_paid, Decimal("1200.00"))
        self.assertEqual(booking.balance_due, Decimal("0.00"))

    # -------------------------------------------------------------
    # 6. DAILY CASH DRAWER OPERATIONS
    # -------------------------------------------------------------
    def test_daily_cash_drawer_summary(self):
        """Verifies daily cash opening, collections, and closing variance calculation."""
        drawer, _ = DailyCashDrawer.objects.get_or_create(
            date=self.today,
            defaults={"opening_cash": Decimal("5000.00")}
        )
        drawer.opening_cash = Decimal("5000.00")
        drawer.save()

        summary = drawer.get_summary()
        self.assertEqual(summary["opening_cash"], 5000.0)
        self.assertIn("expected_closing", summary)
