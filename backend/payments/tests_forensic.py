import hmac
import hashlib
import json
from decimal import Decimal
from datetime import time, timedelta
from unittest.mock import patch
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.models import User, CustomerProfile
from turfs.models import Turf, TimeSlot
from turfs.services import SchedulingEngine
from bookings.models import Booking
from bookings.services import BookingEngine
from payments.models import Payment, Refund, DailyCashDrawer
from payments.razorpay_client import RazorpayService
from payments.reconciliation import ReconciliationEngine
from payments.cancellation import CancellationPolicyEngine
from wallet.models import WalletTransaction


class PaymentForensicIntegrityTests(TestCase):
    """
    Forensic Automated Test Suite for Friends Turf Payment System.
    Verifies financial invariants, cryptographic signatures, idempotency,
    refund limits, overpayment protections, concurrency locks, and role access controls.
    """

    def setUp(self):
        self.client = APIClient()

        # Admin user
        self.admin_user = User.objects.create_user(
            email="forensic_admin@friendsturf.local",
            password="password123",
            role="ADMIN",
        )

        # Staff user
        self.staff_user = User.objects.create_user(
            email="forensic_staff@friendsturf.local",
            password="password123",
            role="STAFF",
        )

        # Customer A
        self.customer_a = User.objects.create_user(
            email="customer_a@friendsturf.local",
            password="password123",
            role="CUSTOMER",
        )
        self.prof_a, _ = CustomerProfile.objects.get_or_create(
            user=self.customer_a,
            defaults={"wallet_balance": Decimal("5000.00")}
        )
        self.prof_a.wallet_balance = Decimal("5000.00")
        self.prof_a.save()

        # Customer B
        self.customer_b = User.objects.create_user(
            email="customer_b@friendsturf.local",
            password="password123",
            role="CUSTOMER",
        )
        self.prof_b, _ = CustomerProfile.objects.get_or_create(
            user=self.customer_b,
            defaults={"wallet_balance": Decimal("1000.00")}
        )

        self.turf = Turf.objects.create(
            name="Forensic Turf - Arena 1",
            slug="forensic-turf-arena-1",
            sport_type="FOOTBALL",
            base_price=Decimal("1000.00"),
            operating_hours_start=time(6, 0),
            operating_hours_end=time(23, 0),
            slot_duration_minutes=60,
        )
        self.match_date = timezone.now().date() + timedelta(days=2)
        self.slots = SchedulingEngine.generate_daily_slots(self.turf, self.match_date)

    # -------------------------------------------------------------
    # 1. CRYPTOGRAPHIC SIGNATURE & ADVERSARIAL FORGERY TESTS
    # -------------------------------------------------------------
    def test_signature_validation_and_forgery_rejection(self):
        """Rejects forged, modified, or replayed signatures."""
        key_secret = "secret_test_xyz"
        order_id = "order_FT_111"
        payment_id = "pay_FT_222"
        valid_sig = hmac.new(
            key_secret.encode("utf-8"),
            f"{order_id}|{payment_id}".encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

        with patch.object(RazorpayService, "get_key_secret", return_value=key_secret):
            # Valid passes
            self.assertTrue(
                RazorpayService.verify_payment_signature(order_id, payment_id, valid_sig)
            )
            # Modified payment ID fails
            self.assertFalse(
                RazorpayService.verify_payment_signature(order_id, "pay_TAMPERED", valid_sig)
            )
            # Modified order ID fails
            self.assertFalse(
                RazorpayService.verify_payment_signature("order_TAMPERED", payment_id, valid_sig)
            )
            # Tampered signature fails
            self.assertFalse(
                RazorpayService.verify_payment_signature(order_id, payment_id, valid_sig[:-4] + "0000")
            )

    # -------------------------------------------------------------
    # 2. SERVER-SIDE PRICE AUTHORITY (NO CLIENT TAMPERING)
    # -------------------------------------------------------------
    def test_client_cannot_tamper_order_amount(self):
        """Server ignores any client-submitted price and uses database rate."""
        self.client.force_authenticate(user=self.customer_a)
        slot = self.slots[0]

        with patch.object(
            RazorpayService,
            "create_order",
            return_value={
                "order_id": "order_mock_safe",
                "amount": 100000,
                "currency": "INR",
                "key_id": "rzp_test_mock",
            },
        ):
            # Attacking with fake amount ₹1.00
            res = self.client.post("/api/payments/razorpay/create-order/", {
                "turf_id": self.turf.id,
                "date": str(self.match_date),
                "slot_ids": [str(slot.id)],
                "amount": "1.00",
                "final_amount": "1.00",
                "payment_type": "FULL",
            })
            self.assertEqual(res.status_code, 201)
            # Booking must be calculated by engine (₹1000.00 base + 18% GST = ₹1180.00)
            booking = Booking.objects.get(booking_id=res.data["booking_id"])
            self.assertEqual(booking.total_amount, Decimal("1000.00"))
            self.assertEqual(res.data["amount_to_pay"], float(booking.final_amount))

    # -------------------------------------------------------------
    # 3. WEBHOOK IDEMPOTENCY & MULTI-REPLAY IMMUNITY
    # -------------------------------------------------------------
    @patch("payments.views.RazorpayService.verify_webhook_signature", return_value=True)
    def test_webhook_replay_does_not_double_count_payment(self, mock_sig):
        """Delivering the same webhook 5 times must only count payment once."""
        slot = self.slots[1]
        booking = Booking.objects.create(
            booking_id="FT-26-REPLAY01",
            customer=self.customer_a,
            turf=self.turf,
            date=self.match_date,
            start_time=slot.start_time,
            end_time=slot.end_time,
            status="PAYMENT_PENDING",
            final_amount=Decimal("1000.00"),
            amount_paid=Decimal("0.00"),
            balance_due=Decimal("1000.00"),
        )
        booking.slots.add(slot)

        payment = Payment.objects.create(
            payment_id="PAY-REPLAY-01",
            booking=booking,
            customer=self.customer_a,
            provider="RAZORPAY",
            provider_order_id="order_REPLAY99",
            amount=Decimal("1000.00"),
            transaction_reference="TXN-REPLAY-99",
            status="PENDING",
        )

        payload = {
            "event": "payment.captured",
            "id": "EVT-REPLAY-UNIQUE-ID-77",
            "payload": {
                "payment": {
                    "entity": {
                        "id": "pay_REPLAY_GATEWAY_1",
                        "order_id": "order_REPLAY99",
                        "amount": 100000,
                    }
                }
            }
        }
        body_bytes = json.dumps(payload).encode("utf-8")

        # 1st Delivery -> processed
        r1 = self.client.post("/api/payments/razorpay/webhook/", data=body_bytes, content_type="application/json")
        self.assertEqual(r1.status_code, 200)
        self.assertEqual(r1.data["status"], "processed")

        # Replays 2, 3, 4, 5 -> already_processed
        for _ in range(4):
            r = self.client.post("/api/payments/razorpay/webhook/", data=body_bytes, content_type="application/json")
            self.assertEqual(r.status_code, 200)
            self.assertEqual(r.data["status"], "already_processed")

        booking.refresh_from_db()
        payment.refresh_from_db()
        self.assertEqual(booking.amount_paid, Decimal("1000.00"))
        self.assertEqual(booking.balance_due, Decimal("0.00"))
        self.assertEqual(booking.status, "CONFIRMED")
        self.assertEqual(payment.status, "PAID")

    # -------------------------------------------------------------
    # 4. WEBHOOK + CLIENT CALLBACK RACE CONDITION
    # -------------------------------------------------------------
    @patch("payments.views.RazorpayService.verify_payment_signature", return_value=True)
    @patch("payments.views.RazorpayService.verify_webhook_signature", return_value=True)
    def test_client_callback_and_webhook_race_integrity(self, mock_wh, mock_sig):
        """Simulate client callback followed by webhook delivery."""
        slot = self.slots[2]
        booking = Booking.objects.create(
            booking_id="FT-26-RACE01",
            customer=self.customer_a,
            turf=self.turf,
            date=self.match_date,
            start_time=slot.start_time,
            end_time=slot.end_time,
            status="PAYMENT_PENDING",
            final_amount=Decimal("1000.00"),
            amount_paid=Decimal("0.00"),
            balance_due=Decimal("1000.00"),
        )
        booking.slots.add(slot)

        payment = Payment.objects.create(
            payment_id="PAY-RACE-01",
            booking=booking,
            customer=self.customer_a,
            provider="RAZORPAY",
            provider_order_id="order_RACE_1",
            amount=Decimal("1000.00"),
            transaction_reference="TXN-RACE-1",
            status="PENDING",
        )

        # Step 1: Client verification callback arrives
        self.client.force_authenticate(user=self.customer_a)
        res_client = self.client.post("/api/payments/razorpay/verify/", {
            "booking_id": booking.booking_id,
            "razorpay_order_id": "order_RACE_1",
            "razorpay_payment_id": "pay_RACE_GATEWAY_1",
            "razorpay_signature": "mock_sig",
        })
        self.assertEqual(res_client.status_code, 200)

        # Step 2: Webhook arrives subsequently
        webhook_payload = {
            "event": "payment.captured",
            "id": "EVT-RACE-WH-1",
            "payload": {
                "payment": {
                    "entity": {
                        "id": "pay_RACE_GATEWAY_1",
                        "order_id": "order_RACE_1",
                        "amount": 100000,
                    }
                }
            }
        }
        res_wh = self.client.post(
            "/api/payments/razorpay/webhook/",
            data=json.dumps(webhook_payload).encode("utf-8"),
            content_type="application/json"
        )
        self.assertEqual(res_wh.status_code, 200)

        booking.refresh_from_db()
        self.assertEqual(booking.amount_paid, Decimal("1000.00"))
        self.assertEqual(booking.balance_due, Decimal("0.00"))
        self.assertEqual(booking.status, "CONFIRMED")

    # -------------------------------------------------------------
    # 5. OFFLINE OVERPAYMENT PROTECTION
    # -------------------------------------------------------------
    def test_manual_collect_rejects_overpayment(self):
        """Staff enters ₹500 for a ₹400 remaining balance -> Must REJECT with 400 error."""
        self.client.force_authenticate(user=self.staff_user)
        booking = Booking.objects.create(
            booking_id="FT-26-OVERPAY01",
            customer=self.customer_a,
            turf=self.turf,
            date=self.match_date,
            start_time=time(10, 0),
            end_time=time(11, 0),
            status="CONFIRMED",
            final_amount=Decimal("1000.00"),
            amount_paid=Decimal("600.00"),
            balance_due=Decimal("400.00"),
        )

        # Attempt to collect ₹500 (which exceeds ₹400 balance)
        res = self.client.post("/api/payments/manual-collect/", {
            "booking_id": booking.booking_id,
            "amount": "500.00",
            "payment_method": "CASH",
        })
        self.assertEqual(res.status_code, 400)
        self.assertIn("exceeds remaining balance due", res.data["error"])

        # Attempt to collect exact ₹400 -> PASS
        res_exact = self.client.post("/api/payments/manual-collect/", {
            "booking_id": booking.booking_id,
            "amount": "400.00",
            "payment_method": "CASH",
        })
        self.assertEqual(res_exact.status_code, 201)
        booking.refresh_from_db()
        self.assertEqual(booking.amount_paid, Decimal("1000.00"))
        self.assertEqual(booking.balance_due, Decimal("0.00"))

        # Subsequent collection attempt when fully paid -> REJECT
        res_after = self.client.post("/api/payments/manual-collect/", {
            "booking_id": booking.booking_id,
            "amount": "100.00",
            "payment_method": "CASH",
        })
        self.assertEqual(res_after.status_code, 400)
        self.assertIn("already fully paid", res_after.data["error"])

    # -------------------------------------------------------------
    # 6. REFUND LIMITS & ROLE PERMISSION ENFORCEMENT
    # -------------------------------------------------------------
    def test_refund_limits_and_staff_forbidden(self):
        """Staff cannot refund; Admin can refund only up to paid amount."""
        booking = Booking.objects.create(
            booking_id="FT-26-REFSEC01",
            customer=self.customer_a,
            turf=self.turf,
            date=self.match_date,
            start_time=time(14, 0),
            end_time=time(15, 0),
            status="CANCELLED",
            final_amount=Decimal("1000.00"),
            amount_paid=Decimal("1000.00"),
            balance_due=Decimal("0.00"),
        )
        payment = Payment.objects.create(
            payment_id="PAY-REFSEC-01",
            booking=booking,
            customer=self.customer_a,
            provider="WALLET",
            amount=Decimal("1000.00"),
            transaction_reference="TXN-REFSEC-01",
            status="PAID",
        )

        # 1. Staff is rejected (403)
        self.client.force_authenticate(user=self.staff_user)
        r_staff = self.client.post(f"/api/payments/{payment.pk}/refund/", {"amount": "1000.00"})
        self.assertEqual(r_staff.status_code, 403)

        # 2. Customer A is rejected (403)
        self.client.force_authenticate(user=self.customer_a)
        r_cust = self.client.post(f"/api/payments/{payment.pk}/refund/", {"amount": "1000.00"})
        self.assertEqual(r_cust.status_code, 403)

        # 3. Admin partial refund ₹400
        self.client.force_authenticate(user=self.admin_user)
        r_admin1 = self.client.post(f"/api/payments/{payment.pk}/refund/", {
            "amount": "400.00",
            "refund_to": "WALLET",
        })
        self.assertEqual(r_admin1.status_code, 201)
        payment.refresh_from_db()
        self.assertEqual(payment.status, "PARTIALLY_REFUNDED")

        # 4. Attempting to refund ₹700 (which exceeds remaining ₹600) -> 400
        r_overflow = self.client.post(f"/api/payments/{payment.pk}/refund/", {
            "amount": "700.00",
            "refund_to": "WALLET",
        })
        self.assertEqual(r_overflow.status_code, 400)

        # 5. Completing remaining ₹600 refund
        r_admin2 = self.client.post(f"/api/payments/{payment.pk}/refund/", {
            "amount": "600.00",
            "refund_to": "WALLET",
        })
        self.assertEqual(r_admin2.status_code, 201)
        payment.refresh_from_db()
        self.assertEqual(payment.status, "REFUNDED")

        # 6. Attempting further refund after full refund -> 400
        r_after = self.client.post(f"/api/payments/{payment.pk}/refund/", {
            "amount": "100.00",
            "refund_to": "WALLET",
        })
        self.assertEqual(r_after.status_code, 400)

    # -------------------------------------------------------------
    # 7. WALLET CHECKOUT & INSUFFICIENT BALANCE
    # -------------------------------------------------------------
    def test_wallet_checkout_atomic_and_insufficient_balance(self):
        """Checks wallet balance, rejects if insufficient, debits atomically on success."""
        slot = self.slots[3]
        self.client.force_authenticate(user=self.customer_b)
        # Customer B only has ₹500 in wallet; Turf is ₹1000 + tax
        self.prof_b.wallet_balance = Decimal("500.00")
        self.prof_b.save()

        # Attempt checkout -> Insufficient balance 400
        res_fail = self.client.post("/api/payments/wallet-checkout/", {
            "turf_id": self.turf.id,
            "date": str(self.match_date),
            "slot_ids": [str(slot.id)],
        })
        self.assertEqual(res_fail.status_code, 400)
        self.assertIn("Insufficient wallet balance", res_fail.data["error"])

        # Customer A has ₹5000 in wallet -> PASS
        self.client.force_authenticate(user=self.customer_a)
        res_ok = self.client.post("/api/payments/wallet-checkout/", {
            "turf_id": self.turf.id,
            "date": str(self.match_date),
            "slot_ids": [str(slot.id)],
        })
        self.assertEqual(res_ok.status_code, 201)
        self.prof_a.refresh_from_db()
        # Booking final is ₹1180 with 18% GST -> wallet balance 5000 - 1180 = 3820
        expected_balance = Decimal("5000.00") - Decimal(str(res_ok.data["booking"]["final_amount"]))
        self.assertEqual(self.prof_a.wallet_balance, expected_balance)

    # -------------------------------------------------------------
    # 8. CUSTOMER OWNERSHIP ISOLATION
    # -------------------------------------------------------------
    def test_customer_cannot_access_other_customer_receipt_or_quote(self):
        """Customer B cannot view Customer A's receipt or cancellation quote."""
        booking = Booking.objects.create(
            booking_id="FT-26-OWNER01",
            customer=self.customer_a,
            turf=self.turf,
            date=self.match_date,
            start_time=time(8, 0),
            end_time=time(9, 0),
            final_amount=Decimal("1000.00"),
            amount_paid=Decimal("1000.00"),
        )
        payment = Payment.objects.create(
            payment_id="PAY-OWNER-01",
            booking=booking,
            customer=self.customer_a,
            provider="CASH",
            amount=Decimal("1000.00"),
            transaction_reference="TXN-OWNER-01",
            status="PAID",
        )

        self.client.force_authenticate(user=self.customer_b)
        # Receipt detail lookup
        r_receipt = self.client.get(f"/api/payments/{payment.payment_id}/receipt/")
        self.assertEqual(r_receipt.status_code, 403)

        # Cancellation quote lookup
        r_quote = self.client.get(f"/api/payments/cancellation-quote/{booking.booking_id}/")
        self.assertEqual(r_quote.status_code, 403)

    # -------------------------------------------------------------
    # 9. CANCELLED BOOKING PAYMENT ARRIVAL PROTECTION
    # -------------------------------------------------------------
    @patch("payments.views.RazorpayService.verify_payment_signature", return_value=True)
    def test_cancelled_booking_payment_credit_to_wallet(self, mock_sig):
        """If a booking was cancelled while checkout was pending, payment arrival auto-credits wallet."""
        slot = self.slots[4]
        booking = Booking.objects.create(
            booking_id="FT-26-CANCVER01",
            customer=self.customer_a,
            turf=self.turf,
            date=self.match_date,
            start_time=slot.start_time,
            end_time=slot.end_time,
            status="CANCELLED",
            final_amount=Decimal("1000.00"),
            amount_paid=Decimal("0.00"),
            balance_due=Decimal("1000.00"),
        )
        payment = Payment.objects.create(
            payment_id="PAY-CANCVER-01",
            booking=booking,
            customer=self.customer_a,
            provider="RAZORPAY",
            provider_order_id="order_CANCVER_1",
            amount=Decimal("1000.00"),
            transaction_reference="TXN-CANCVER-1",
            status="PENDING",
        )

        initial_wallet = self.prof_a.wallet_balance
        self.client.force_authenticate(user=self.customer_a)
        res = self.client.post("/api/payments/razorpay/verify/", {
            "booking_id": booking.booking_id,
            "razorpay_order_id": "order_CANCVER_1",
            "razorpay_payment_id": "pay_CANCVER_GATEWAY_1",
            "razorpay_signature": "mock_sig",
        })

        self.assertEqual(res.status_code, 400)
        self.assertEqual(res.data["code"], "BOOKING_CANCELLED_REFUNDED_TO_WALLET")

        # Verify wallet balance increased by ₹1000
        self.prof_a.refresh_from_db()
        self.assertEqual(self.prof_a.wallet_balance, initial_wallet + Decimal("1000.00"))

        # Verify booking remained cancelled
        booking.refresh_from_db()
        self.assertEqual(booking.status, "CANCELLED")

    # -------------------------------------------------------------
    # 10. EXPIRED HOLD SLOT CONFLICT PROTECTION
    # -------------------------------------------------------------
    @patch("payments.views.RazorpayService.verify_payment_signature", return_value=True)
    def test_expired_hold_slot_conflict_auto_refund_to_wallet(self, mock_sig):
        """If customer hold expired and another user booked the slot, payment is credited to wallet without double-booking."""
        slot = self.slots[5]
        # Customer A had a pending booking
        booking_a = Booking.objects.create(
            booking_id="FT-26-EXPIRA",
            customer=self.customer_a,
            turf=self.turf,
            date=self.match_date,
            start_time=slot.start_time,
            end_time=slot.end_time,
            status="PAYMENT_PENDING",
            final_amount=Decimal("1000.00"),
            amount_paid=Decimal("0.00"),
            balance_due=Decimal("1000.00"),
        )
        booking_a.slots.add(slot)

        payment_a = Payment.objects.create(
            payment_id="PAY-EXPIRA-01",
            booking=booking_a,
            customer=self.customer_a,
            provider="RAZORPAY",
            provider_order_id="order_EXPIRA_1",
            amount=Decimal("1000.00"),
            transaction_reference="TXN-EXPIRA-1",
            status="PENDING",
        )

        # In the meantime, slot hold expired and Customer B booked the slot
        slot.status = "BOOKED"
        slot.booking_id = "FT-26-CUSTOMER-B-BOOKING"
        slot.save()

        initial_wallet = self.prof_a.wallet_balance
        self.client.force_authenticate(user=self.customer_a)
        res = self.client.post("/api/payments/razorpay/verify/", {
            "booking_id": booking_a.booking_id,
            "razorpay_order_id": "order_EXPIRA_1",
            "razorpay_payment_id": "pay_EXPIRA_GATEWAY_1",
            "razorpay_signature": "mock_sig",
        })

        self.assertEqual(res.status_code, 409)
        self.assertEqual(res.data["code"], "SLOT_CLAIMED_REFUNDED_TO_WALLET")

        # Verify wallet credited
        self.prof_a.refresh_from_db()
        self.assertEqual(self.prof_a.wallet_balance, initial_wallet + Decimal("1000.00"))

        # Verify slot still belongs to Customer B
        slot.refresh_from_db()
        self.assertEqual(slot.booking_id, "FT-26-CUSTOMER-B-BOOKING")

    # -------------------------------------------------------------
    # 11. MISMATCHED RAZORPAY ORDER ID REJECTION
    # -------------------------------------------------------------
    def test_mismatched_order_id_rejection(self):
        """Rejects verification when razorpay_order_id does not match booking payment intent."""
        slot = self.slots[6]
        booking = Booking.objects.create(
            booking_id="FT-26-MISORDER01",
            customer=self.customer_a,
            turf=self.turf,
            date=self.match_date,
            start_time=slot.start_time,
            end_time=slot.end_time,
            status="PAYMENT_PENDING",
            final_amount=Decimal("1000.00"),
            amount_paid=Decimal("0.00"),
            balance_due=Decimal("1000.00"),
        )
        Payment.objects.create(
            payment_id="PAY-MISORDER-01",
            booking=booking,
            customer=self.customer_a,
            provider="RAZORPAY",
            provider_order_id="order_REAL_INTENT_123",
            amount=Decimal("1000.00"),
            transaction_reference="TXN-MISORDER-01",
            status="PENDING",
        )

        self.client.force_authenticate(user=self.customer_a)
        res = self.client.post("/api/payments/razorpay/verify/", {
            "booking_id": booking.booking_id,
            "razorpay_order_id": "order_FAKE_FORGED_999",
            "razorpay_payment_id": "pay_GATEWAY_1",
            "razorpay_signature": "sig",
        })
        self.assertEqual(res.status_code, 404)

    # -------------------------------------------------------------
    # 12. PRICE OVERRIDE PRIVILEGE ENFORCEMENT
    # -------------------------------------------------------------
    def test_staff_unauthorized_price_override_rejected(self):
        """Staff members are forbidden from performing manual price overrides in PricingEngine."""
        from pricing.engine import PricingEngine

        slot = self.slots[7]
        # Staff attempting price override raises ValueError
        with self.assertRaises(ValueError):
            PricingEngine.calculate_booking_total(
                turf=self.turf,
                date_obj=self.match_date,
                slot_items=[{"start_time": slot.start_time, "end_time": slot.end_time}],
                manual_override_price=Decimal("100.00"),
                actor=self.staff_user,
            )

        # Admin is permitted
        admin_calc = PricingEngine.calculate_booking_total(
            turf=self.turf,
            date_obj=self.match_date,
            slot_items=[{"start_time": slot.start_time, "end_time": slot.end_time}],
            manual_override_price=Decimal("500.00"),
            actor=self.admin_user,
        )
        self.assertEqual(admin_calc["final_amount"], 500.0)
        self.assertTrue(admin_calc["is_price_overridden"])

    # -------------------------------------------------------------
    # 13. CANCELLATION DYNAMIC POLICY BOUNDARY TIERS
    # -------------------------------------------------------------
    def test_cancellation_dynamic_policy_boundary_tiers(self):
        """Tests cancellation refund calculations at all 4 lead-time boundaries."""
        from payments.cancellation import CancellationPolicyEngine

        booking = Booking.objects.create(
            booking_id="FT-26-TIERS01",
            customer=self.customer_a,
            turf=self.turf,
            date=self.match_date,
            start_time=time(18, 0),
            end_time=time(19, 0),
            final_amount=Decimal("1000.00"),
            amount_paid=Decimal("1000.00"),
        )
        kickoff = timezone.make_aware(timezone.datetime.combine(self.match_date, time(18, 0)))

        # Tier 1: > 24h before kickoff (Full refund, 0% fee)
        q1 = CancellationPolicyEngine.calculate_refund(booking, as_of_time=kickoff - timedelta(hours=30))
        self.assertEqual(q1["eligibility"], "FULL")
        self.assertEqual(q1["cancellation_fee"], 0.0)
        self.assertEqual(q1["refundable_amount"], 1000.0)

        # Tier 2: 12h before kickoff (20% fee, 80% refund)
        q2 = CancellationPolicyEngine.calculate_refund(booking, as_of_time=kickoff - timedelta(hours=12))
        self.assertEqual(q2["eligibility"], "PARTIAL")
        self.assertEqual(q2["cancellation_fee"], 200.0)
        self.assertEqual(q2["refundable_amount"], 800.0)

        # Tier 3: 3h before kickoff (50% fee, 50% refund)
        q3 = CancellationPolicyEngine.calculate_refund(booking, as_of_time=kickoff - timedelta(hours=3))
        self.assertEqual(q3["eligibility"], "PARTIAL")
        self.assertEqual(q3["cancellation_fee"], 500.0)
        self.assertEqual(q3["refundable_amount"], 500.0)

        # Tier 4: < 2h before kickoff (100% fee, Non-refundable)
        q4 = CancellationPolicyEngine.calculate_refund(booking, as_of_time=kickoff - timedelta(hours=1))
        self.assertEqual(q4["eligibility"], "NONE")
        self.assertEqual(q4["cancellation_fee"], 1000.0)
        self.assertEqual(q4["refundable_amount"], 0.0)

    # -------------------------------------------------------------
    # 14. DAILY CASH DRAWER DISCREPANCY DETECTION
    # -------------------------------------------------------------
    def test_daily_cash_drawer_discrepancy_flagging(self):
        """Flags DISCREPANCY status when actual cash count differs from expected closing."""
        today = timezone.now().date()
        drawer, _ = DailyCashDrawer.objects.get_or_create(date=today)
        drawer.opening_cash = Decimal("2000.00")
        drawer.save()

        # Staff records cash collection of ₹1000
        Payment.objects.create(
            payment_id="PAY-DRAWER-CASH-1",
            customer=self.customer_a,
            provider="CASH",
            amount=Decimal("1000.00"),
            payment_method="CASH",
            status="PAID",
            created_at=timezone.now(),
        )

        # Expected closing: 2000 + 1000 = 3000
        # Staff closes drawer with ₹2500 (missing ₹500)
        self.client.force_authenticate(user=self.staff_user)
        res = self.client.post("/api/payments/daily-cash/", {
            "action": "CLOSE_DRAWER",
            "actual_closing_cash": "2500.00",
            "notes": "Shortage of 500 found at shift end",
        })

        self.assertEqual(res.status_code, 200)
        drawer.refresh_from_db()
        self.assertEqual(drawer.status, "DISCREPANCY")
        self.assertEqual(res.data["variance"], -500.0)
        self.assertTrue(res.data["has_discrepancy"])

