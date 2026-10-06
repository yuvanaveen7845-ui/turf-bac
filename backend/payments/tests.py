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
        self.today = timezone.now().date() + timedelta(days=1)
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

        body_bytes = json.dumps(webhook_payload).encode("utf-8")
        from django.conf import settings
        webhook_secret = getattr(settings, "RAZORPAY_WEBHOOK_SECRET", "")
        sig = hmac.new(webhook_secret.encode("utf-8"), body_bytes, hashlib.sha256).hexdigest()

        # Send webhook
        res1 = self.client.post(
            "/api/payments/razorpay/webhook/",
            data=body_bytes,
            content_type="application/json",
            HTTP_X_RAZORPAY_SIGNATURE=sig,
        )
        self.assertEqual(res1.status_code, 200)
        self.assertEqual(res1.data["status"], "processed")

        # Verify booking and payment are confirmed
        payment.refresh_from_db()
        booking.refresh_from_db()
        self.assertEqual(payment.status, "PAID")
        self.assertEqual(booking.status, "CONFIRMED")
        self.assertEqual(booking.slots.first().status, "BOOKED")

        # Idempotency test: duplicate webhook delivers status: already_processed
        res2 = self.client.post(
            "/api/payments/razorpay/webhook/",
            data=body_bytes,
            content_type="application/json",
            HTTP_X_RAZORPAY_SIGNATURE=sig,
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

    # -------------------------------------------------------------
    # 7. EXPRESS GUEST CHECKOUT (UNAUTHENTICATED)
    # -------------------------------------------------------------
    def test_guest_checkout_order_creation_and_pass(self):
        """Verifies an unauthenticated visitor can lock a slot, create Razorpay order, verify payment, and get match pass."""
        guest_client = APIClient()  # No force_authenticate
        target_slot = self.slots[4]

        # 1. Guest locks slot
        lock_res = guest_client.post("/api/bookings/lock/", {
            "turf_id": self.turf.id,
            "date": str(self.today),
            "slot_ids": [target_slot.id],
        }, format="json")
        self.assertEqual(lock_res.status_code, 200)

        # 2. Guest creates order
        with patch.object(RazorpayService, "create_order", return_value={"order_id": "order_guest_999", "amount": 120000, "currency": "INR", "key_id": "rzp_test_key"}):
            order_res = guest_client.post("/api/payments/razorpay/create-order/", {
                "turf_id": self.turf.id,
                "date": str(self.today),
                "slot_ids": [target_slot.id],
                "payment_type": "FULL",
                "customer_name": "Karthik Striker",
                "customer_phone": "9842211223",
                "customer_email": "karthik@gmail.com",
            }, format="json")
            self.assertEqual(order_res.status_code, 201)
            order_data = order_res.json()
            booking_id = order_data["booking_id"]

        # Check guest customer was created
        guest_user = User.objects.filter(phone__endswith="9842211223").first()
        self.assertIsNotNone(guest_user)
        self.assertEqual(guest_user.role, "CUSTOMER")
        self.assertIn("Karthik Striker", guest_user.get_full_name())

        # 3. Guest verifies payment
        with patch.object(RazorpayService, "verify_payment_signature", return_value=True):
            verify_res = guest_client.post("/api/payments/razorpay/verify/", {
                "razorpay_order_id": "order_guest_999",
                "razorpay_payment_id": "pay_guest_777",
                "razorpay_signature": "valid_signature",
                "booking_id": booking_id,
            }, format="json")
            self.assertEqual(verify_res.status_code, 200)

        # 4. Guest retrieves match pass without authentication
        pass_res = guest_client.get(f"/api/qr/pass/{booking_id}/")
        self.assertEqual(pass_res.status_code, 200)
        pass_data = pass_res.json()
        self.assertEqual(pass_data["booking_id"], booking_id)
        self.assertEqual(pass_data["status"], "ACTIVE")
        self.assertEqual(pass_data["booking_status"], "CONFIRMED")
        self.assertEqual(pass_data["payment_status"], "PAID")
        self.assertIn("9842211223", pass_data["customer_phone"])

        # 5. Guest looks up match pass by 10-digit mobile number
        lookup_phone_res = guest_client.post("/api/bookings/lookup/", {
            "query": "9842211223",
        }, format="json")
        self.assertEqual(lookup_phone_res.status_code, 200)
        phone_results = lookup_phone_res.json().get("results", [])
        self.assertTrue(len(phone_results) >= 1)
        self.assertEqual(phone_results[0]["booking_id"], booking_id)

        # 6. Guest looks up match pass by Booking ID
        lookup_id_res = guest_client.post("/api/bookings/lookup/", {
            "query": booking_id,
        }, format="json")
        self.assertEqual(lookup_id_res.status_code, 200)
        id_results = lookup_id_res.json().get("results", [])
        self.assertEqual(len(id_results), 1)
        self.assertEqual(id_results[0]["booking_id"], booking_id)

        # 7. Guest accesses tax invoice receipt without authentication
        receipt_res = guest_client.get(f"/api/payments/{booking_id}/receipt/")
        self.assertEqual(receipt_res.status_code, 200)
        self.assertIn("receipt_number", receipt_res.json())

    def test_guest_partial_advance_and_balance_settlement(self):
        """Verifies a guest can pay an advance deposit, retrieve partial pass, and settle remaining balance without login."""
        guest_client = APIClient()
        target_slot = self.slots[5]

        # 1. Lock slot
        guest_client.post("/api/bookings/lock/", {
            "turf_id": self.turf.id,
            "date": str(self.today),
            "slot_ids": [target_slot.id],
        }, format="json")

        # 2. Create partial advance order (₹200 deposit)
        with patch.object(RazorpayService, "create_order", return_value={"order_id": "order_advance_111", "amount": 20000, "currency": "INR", "key_id": "rzp_test_key"}):
            order_res = guest_client.post("/api/payments/razorpay/create-order/", {
                "turf_id": self.turf.id,
                "date": str(self.today),
                "slot_ids": [target_slot.id],
                "payment_type": "PARTIAL",
                "advance_amount": 200,
                "customer_name": "Rahman Midfielder",
                "customer_phone": "9842299887",
            }, format="json")
            self.assertEqual(order_res.status_code, 201)
            booking_id = order_res.json()["booking_id"]

        # 3. Verify advance payment
        with patch.object(RazorpayService, "verify_payment_signature", return_value=True):
            verify_res = guest_client.post("/api/payments/razorpay/verify/", {
                "razorpay_order_id": "order_advance_111",
                "razorpay_payment_id": "pay_advance_222",
                "razorpay_signature": "sig_adv",
                "booking_id": booking_id,
            }, format="json")
            self.assertEqual(verify_res.status_code, 200)

        # 4. Check partial pass state
        pass_res = guest_client.get(f"/api/qr/pass/{booking_id}/")
        self.assertEqual(pass_res.status_code, 200)
        pass_data = pass_res.json()
        self.assertEqual(pass_data["status"], "DEPOSIT_CONFIRMED")
        self.assertEqual(pass_data["payment_status"], "PARTIAL")
        self.assertEqual(pass_data["amount_paid"], 200.0)
        self.assertEqual(pass_data["balance_due"], 1000.0)

        # 5. Guest clears remaining balance (₹1,000) online without login
        with patch.object(RazorpayService, "create_order", return_value={"order_id": "order_bal_333", "amount": 100000, "currency": "INR", "key_id": "rzp_test_key"}):
            bal_order_res = guest_client.post("/api/payments/razorpay/pay-balance/", {
                "booking_id": booking_id,
            }, format="json")
            self.assertEqual(bal_order_res.status_code, 201)

        # 6. Verify balance payment
        with patch.object(RazorpayService, "verify_payment_signature", return_value=True):
            bal_verify_res = guest_client.post("/api/payments/razorpay/verify-balance/", {
                "razorpay_order_id": "order_bal_333",
                "razorpay_payment_id": "pay_bal_444",
                "razorpay_signature": "sig_bal",
                "booking_id": booking_id,
            }, format="json")
            self.assertEqual(bal_verify_res.status_code, 200)

        # 7. Match pass is now 100% PAID and ACTIVE
        pass_res_final = guest_client.get(f"/api/qr/pass/{booking_id}/")
        self.assertEqual(pass_res_final.status_code, 200)
        final_pass_data = pass_res_final.json()
        self.assertEqual(final_pass_data["status"], "ACTIVE")
        self.assertEqual(final_pass_data["payment_status"], "PAID")
        self.assertEqual(final_pass_data["balance_due"], 0.0)
        self.assertEqual(final_pass_data["amount_paid"], 1200.0)

        # 8. Attempting to pay balance when balance is 0 fails with 400
        zero_bal_res = guest_client.post("/api/payments/razorpay/pay-balance/", {
            "booking_id": booking_id,
        }, format="json")
        self.assertEqual(zero_bal_res.status_code, 400)
        self.assertIn("no outstanding balance", zero_bal_res.data["error"])

        # 9. Guest retrieves official branded tax receipt without login
        receipt_res = guest_client.get(f"/api/payments/{booking_id}/receipt/")
        self.assertEqual(receipt_res.status_code, 200)
        self.assertEqual(receipt_res.data["booking"]["booking_id"], booking_id)
        self.assertIn("receipt_number", receipt_res.data)

        # 10. Attempting to pay balance on a CANCELLED booking fails with 400
        booking = Booking.objects.get(booking_id=booking_id)
        booking.status = "CANCELLED"
        booking.balance_due = Decimal("500.00")
        booking.save()
        cancelled_pay_res = guest_client.post("/api/payments/razorpay/pay-balance/", {
            "booking_id": booking_id,
        }, format="json")
        self.assertEqual(cancelled_pay_res.status_code, 400)
        self.assertIn("CANCELLED", cancelled_pay_res.data["error"])

    # -------------------------------------------------------------
    # 8. 30-MINUTE INTERVAL & SPLIT SURGE PAYMENT TESTS
    # -------------------------------------------------------------
    def test_30min_slots_razorpay_order_and_payment_flow(self):
        """Verifies 30-minute contiguous slots (10:30-11:30 = 2 slots) work seamlessly with Razorpay checkout."""
        turf_30m = Turf.objects.create(
            name="Turf 30m Pro Arena",
            slug="turf-30m-pro-arena",
            sport_type="FOOTBALL",
            base_price=Decimal("1200.00"),
            operating_hours_start=time(6, 0),
            operating_hours_end=time(23, 0),
            slot_duration_minutes=30,
        )
        future_date = timezone.now().date() + timedelta(days=2)
        slots_30m = SchedulingEngine.generate_daily_slots(turf_30m, future_date)

        s1 = next(s for s in slots_30m if s.start_time == time(10, 30))
        s2 = next(s for s in slots_30m if s.start_time == time(11, 0))

        client = APIClient()
        client.force_authenticate(user=self.customer)

        # 1. Lock 60m contiguous slots
        lock_res = client.post("/api/bookings/lock/", {
            "turf_id": turf_30m.id,
            "date": str(future_date),
            "slot_ids": [s1.id, s2.id],
        }, format="json")
        self.assertEqual(lock_res.status_code, 200)

        # 2. Create Razorpay order for 60m match (₹1,200)
        with patch.object(RazorpayService, "create_order", return_value={"order_id": "order_30m_99", "amount": 120000, "currency": "INR", "key_id": "rzp_test_key"}):
            order_res = client.post("/api/payments/razorpay/create-order/", {
                "turf_id": turf_30m.id,
                "date": str(future_date),
                "slot_ids": [s1.id, s2.id],
                "payment_type": "FULL",
            }, format="json")
            self.assertEqual(order_res.status_code, 201)
            b_id = order_res.json()["booking_id"]

        # 3. Verify Payment
        with patch.object(RazorpayService, "verify_payment_signature", return_value=True):
            verify_res = client.post("/api/payments/razorpay/verify/", {
                "razorpay_order_id": "order_30m_99",
                "razorpay_payment_id": "pay_30m_99",
                "razorpay_signature": "sig_30m",
                "booking_id": b_id,
            }, format="json")
            self.assertEqual(verify_res.status_code, 200)

        booking = Booking.objects.get(booking_id=b_id)
        self.assertEqual(booking.status, "CONFIRMED")
        self.assertEqual(booking.start_time, time(10, 30))
        self.assertEqual(booking.end_time, time(11, 30))
        self.assertEqual(booking.final_amount, Decimal("1200.00"))
        self.assertEqual(booking.amount_paid, Decimal("1200.00"))
        self.assertEqual(booking.balance_due, Decimal("0.00"))

    def test_30min_slots_wallet_checkout(self):
        """Verifies customer can pay for 30-minute interval booking using Turf Cash Wallet balance."""
        turf_30m = Turf.objects.create(
            name="Turf 30m Wallet Arena",
            slug="turf-30m-wallet-arena",
            sport_type="FOOTBALL",
            base_price=Decimal("1000.00"),
            operating_hours_start=time(6, 0),
            operating_hours_end=time(23, 0),
            slot_duration_minutes=30,
        )
        future_date = timezone.now().date() + timedelta(days=3)
        slots_30m = SchedulingEngine.generate_daily_slots(turf_30m, future_date)

        s1 = next(s for s in slots_30m if s.start_time == time(16, 0))
        s2 = next(s for s in slots_30m if s.start_time == time(16, 30))

        # Credit wallet
        prof = self.customer.customer_profile
        prof.wallet_balance = Decimal("2000.00")
        prof.save()

        client = APIClient()
        client.force_authenticate(user=self.customer)

        # Checkout via wallet
        res = client.post("/api/payments/wallet-checkout/", {
            "turf_id": turf_30m.id,
            "date": str(future_date),
            "slot_ids": [s1.id, s2.id],
        }, format="json")
        self.assertEqual(res.status_code, 201)

        b_id = res.json()["booking"]["booking_id"]
        booking = Booking.objects.get(booking_id=b_id)
        self.assertEqual(booking.status, "CONFIRMED")
        self.assertEqual(booking.final_amount, Decimal("1000.00"))
        self.assertEqual(booking.amount_paid, Decimal("1000.00"))

        prof.refresh_from_db()
        self.assertEqual(prof.wallet_balance, Decimal("1000.00"))




