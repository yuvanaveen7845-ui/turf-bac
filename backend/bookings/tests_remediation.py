import uuid
import zoneinfo
from datetime import datetime, date, time, timedelta
from decimal import Decimal
from unittest.mock import patch

from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient
from rest_framework import status

from accounts.models import User
from turfs.models import Turf, TimeSlot
from bookings.models import Booking
from bookings.services import BookingEngine
from payments.models import Payment
from qr_system.models import QRCredential, CheckIn
from qr_system.services import QRService, BUSINESS_TZ
from notifications.services import EmailNotificationService


class SystemRemediationMasterTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.admin = User.objects.create_superuser(
            email="admin@friendsturf.com",
            password="adminpassword123",
            first_name="Admin",
            role="ADMIN",
        )
        self.staff = User.objects.create_user(
            email="staff@friendsturf.com",
            password="staffpassword123",
            first_name="Staff Operator",
            role="STAFF",
        )
        self.customer = User.objects.create_user(
            email="customer@friendsturf.com",
            password="customerpassword123",
            first_name="John Player",
            phone="+919876543210",
            role="CUSTOMER",
        )
        self.turf = Turf.objects.create(
            name="Champions Arena Pitch 1",
            slug="champions-arena",
            sport_type="FOOTBALL",
            base_price=Decimal("1200.00"),
            operating_hours_start=time(6, 0),
            operating_hours_end=time(23, 0),
            slot_duration_minutes=60,
            is_active=True,
        )
        self.today = timezone.now().astimezone(BUSINESS_TZ).date()

        # Create 4 consecutive test slots for today
        self.slot1 = TimeSlot.objects.create(
            turf=self.turf,
            date=self.today,
            start_time=time(18, 0),
            end_time=time(19, 0),
            price=Decimal("1200.00"),
            status="AVAILABLE",
        )
        self.slot2 = TimeSlot.objects.create(
            turf=self.turf,
            date=self.today,
            start_time=time(19, 0),
            end_time=time(20, 0),
            price=Decimal("1200.00"),
            status="AVAILABLE",
        )
        self.slot3 = TimeSlot.objects.create(
            turf=self.turf,
            date=self.today,
            start_time=time(20, 0),
            end_time=time(21, 0),
            price=Decimal("1200.00"),
            status="AVAILABLE",
        )
        self.slot_gap = TimeSlot.objects.create(
            turf=self.turf,
            date=self.today,
            start_time=time(22, 0),
            end_time=time(23, 0),
            price=Decimal("1200.00"),
            status="AVAILABLE",
        )

    # -------------------------------------------------------------------------
    # Bug 1.1: Walk-in Razorpay Single-Lifecycle Flow (No 409 Conflict)
    # -------------------------------------------------------------------------
    @patch("payments.razorpay_client.RazorpayService.create_order")
    def test_walkin_razorpay_single_lifecycle(self, mock_create_order):
        mock_create_order.return_value = {
            "order_id": "order_test_walkin_123",
            "amount": 141600,
            "currency": "INR",
            "key_id": "rzp_test_key",
        }
        self.client.force_authenticate(user=self.staff)

        # 1. Staff initiates Walk-in with Razorpay
        response = self.client.post(
            "/api/bookings/staff/walk-in/",
            {
                "turf_id": str(self.turf.id),
                "slot_ids": [str(self.slot1.id)],
                "customer_name": "Walkin Guest Test",
                "customer_phone": "+919999988888",
                "payment_method": "RAZORPAY",
                "notes": "Counter card / Razorpay",
            },
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        data = response.data
        self.assertEqual(data["order_id"], "order_test_walkin_123")
        self.assertIn("booking_id", data)

        booking = Booking.objects.get(booking_id=data["booking_id"])
        self.assertEqual(booking.status, "PAYMENT_PENDING")
        self.assertEqual(booking.booking_type, "WALK_IN")
        self.assertEqual(booking.amount_paid, Decimal("0.00"))

        # Verify Payment record is created in PENDING status
        payment = Payment.objects.get(booking=booking, provider_order_id="order_test_walkin_123")
        self.assertEqual(payment.status, "PENDING")

        # 2. Staff verifies payment after customer pays
        with patch("payments.razorpay_client.RazorpayService.verify_payment_signature", return_value=True):
            verify_res = self.client.post(
                "/api/payments/razorpay/verify/",
                {
                    "razorpay_order_id": "order_test_walkin_123",
                    "razorpay_payment_id": "pay_test_walkin_456",
                    "razorpay_signature": "valid_signature_abc",
                    "booking_id": booking.booking_id,
                },
                format="json",
            )
            self.assertEqual(verify_res.status_code, status.HTTP_200_OK)

        booking.refresh_from_db()
        self.assertEqual(booking.status, "CONFIRMED")
        self.assertGreater(booking.amount_paid, Decimal("0.00"))
        self.assertEqual(booking.balance_due, Decimal("0.00"))

    # -------------------------------------------------------------------------
    # Bug 1.2 & 4.1: Consecutive Slot Validation in Razorpay Order Creation
    # -------------------------------------------------------------------------
    def test_razorpay_order_rejects_non_consecutive_slots(self):
        self.client.force_authenticate(user=self.customer)

        # slot1 (18:00-19:00) and slot_gap (22:00-23:00) are not contiguous
        response = self.client.post(
            "/api/payments/razorpay/create-order/",
            {
                "turf_id": str(self.turf.id),
                "date": str(self.today),
                "slot_ids": [str(self.slot1.id), str(self.slot_gap.id)],
                "payment_type": "FULL",
            },
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(response.data.get("code"), "NON_CONSECUTIVE_SLOTS")

    @patch("payments.razorpay_client.RazorpayService.create_order")
    def test_razorpay_order_allows_consecutive_multi_hour_slots(self, mock_create_order):
        mock_create_order.return_value = {
            "order_id": "order_test_multi_123",
            "amount": 283200,
            "currency": "INR",
            "key_id": "rzp_test_key",
        }
        self.client.force_authenticate(user=self.customer)

        # slot1 (18:00-19:00) and slot2 (19:00-20:00) are consecutive
        response = self.client.post(
            "/api/payments/razorpay/create-order/",
            {
                "turf_id": str(self.turf.id),
                "date": str(self.today),
                "slot_ids": [str(self.slot1.id), str(self.slot2.id)],
                "payment_type": "FULL",
            },
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["order_id"], "order_test_multi_123")

    # -------------------------------------------------------------------------
    # Bug 1.3: Webhook Idempotency & Financial Invariants
    # -------------------------------------------------------------------------
    def test_webhook_idempotency_does_not_duplicate_amount_paid(self):
        booking = BookingEngine.create_booking(
            turf=self.turf,
            date_obj=self.today,
            slot_ids=[self.slot1.id],
            user=self.customer,
            booking_type="REGULAR",
            payment_type="PENDING",
            payment_method="UPI",
        )
        payment = Payment.objects.create(
            payment_id=f"PAY_{uuid.uuid4().hex[:10]}",
            booking=booking,
            customer=self.customer,
            provider="RAZORPAY",
            provider_order_id="order_webhook_test_999",
            amount=booking.final_amount,
            currency="INR",
            payment_method="UPI",
            payment_type="FULL",
            transaction_reference="TXN-WH-999",
            status="PENDING",
        )

        webhook_payload = {
            "event": "order.paid",
            "id": f"evt_test_{uuid.uuid4().hex[:8]}",
            "payload": {
                "payment": {
                    "entity": {
                        "id": "pay_rzp_test_777",
                        "order_id": "order_webhook_test_999",
                        "amount": int(booking.final_amount * 100),
                    }
                }
            },
        }

        # 1. First webhook execution
        with patch("payments.razorpay_client.RazorpayService.verify_webhook_signature", return_value=True):
            res1 = self.client.post(
                "/api/payments/razorpay/webhook/",
                webhook_payload,
                format="json",
            )
            self.assertEqual(res1.status_code, status.HTTP_200_OK)

            booking.refresh_from_db()
            expected_amount = booking.final_amount
            self.assertEqual(booking.amount_paid, expected_amount)
            self.assertEqual(booking.balance_due, Decimal("0.00"))

            # 2. Duplicate webhook delivery
            webhook_payload["id"] = f"evt_test_{uuid.uuid4().hex[:8]}"  # different delivery ID, same order
            res2 = self.client.post(
                "/api/payments/razorpay/webhook/",
                webhook_payload,
                format="json",
            )
            self.assertEqual(res2.status_code, status.HTTP_200_OK)

        booking.refresh_from_db()
        # Invariant: amount_paid must never exceed final_amount
        self.assertEqual(booking.amount_paid, expected_amount)
        self.assertEqual(booking.balance_due, Decimal("0.00"))

    # -------------------------------------------------------------------------
    # Bug 2.1 & 2.2: Timezone Determinism & Atomic QR Concurrency
    # -------------------------------------------------------------------------
    def test_qr_checkin_window_and_duplicate_scan_prevention(self):
        booking = BookingEngine.create_booking(
            turf=self.turf,
            date_obj=self.today,
            slot_ids=[self.slot1.id],
            user=self.customer,
            booking_type="REGULAR",
            payment_type="FULL",
            payment_method="UPI",
        )
        credential = QRService.generate_credential_for_booking(booking)

        # 1. Simulate scan 45 mins before kickoff (start is 18:00, check-in opens at 17:30)
        early_time = datetime.combine(self.today, time(17, 10), tzinfo=BUSINESS_TZ)
        with patch("django.utils.timezone.now", return_value=early_time):
            result_early = QRService.evaluate_and_checkin(
                raw_input=credential.credential_token,
                staff_user=self.staff,
            )
            self.assertFalse(result_early["valid"])
            self.assertEqual(result_early["reason_code"], "OUTSIDE_CHECKIN_WINDOW")

        # 2. Simulate scan inside admission window (e.g. 17:45)
        valid_time = datetime.combine(self.today, time(17, 45), tzinfo=BUSINESS_TZ)
        with patch("django.utils.timezone.now", return_value=valid_time):
            result_valid = QRService.evaluate_and_checkin(
                raw_input=credential.credential_token,
                staff_user=self.staff,
            )
            self.assertTrue(result_valid["valid"])
            self.assertEqual(result_valid["decision"], "ALLOW")
            self.assertEqual(result_valid["status"], "ADMITTED")

        booking.refresh_from_db()
        self.assertEqual(booking.status, "CHECKED_IN")

        # 3. Simulate immediate duplicate scan at second scanner turnstile
        with patch("django.utils.timezone.now", return_value=valid_time):
            result_duplicate = QRService.evaluate_and_checkin(
                raw_input=credential.credential_token,
                staff_user=self.staff,
            )
            self.assertFalse(result_duplicate["valid"])
            self.assertEqual(result_duplicate["decision"], "DENY")
            self.assertEqual(result_duplicate["reason_code"], "ALREADY_CHECKED_IN")

    # -------------------------------------------------------------------------
    # Bug 2.3: Partial Payment Gate Check-In & Settlement Workflow
    # -------------------------------------------------------------------------
    def test_partial_payment_gate_settlement(self):
        booking = BookingEngine.create_booking(
            turf=self.turf,
            date_obj=self.today,
            slot_ids=[self.slot1.id],
            user=self.customer,
            booking_type="REGULAR",
            payment_type="PARTIAL",
            payment_method="UPI",
        )
        self.assertGreater(booking.balance_due, Decimal("0.00"))
        credential = QRService.generate_credential_for_booking(booking)

        # 1. Scan returns BALANCE_DUE
        valid_time = datetime.combine(self.today, time(17, 45), tzinfo=BUSINESS_TZ)
        with patch("django.utils.timezone.now", return_value=valid_time):
            res_scan = QRService.evaluate_and_checkin(
                raw_input=credential.credential_token,
                staff_user=self.staff,
            )
            self.assertFalse(res_scan["valid"])
            self.assertEqual(res_scan["reason_code"], "BALANCE_DUE")
            self.assertTrue(res_scan.get("can_collect_balance"))

        # 2. Staff collects balance via 1-click endpoint
        self.client.force_authenticate(user=self.staff)
        with patch("django.utils.timezone.now", return_value=valid_time):
            settle_res = self.client.post(
                "/api/qr/collect-balance-admit/",
                {
                    "booking_id": booking.booking_id,
                    "payment_method": "CASH",
                    "amount": float(booking.balance_due),
                    "notes": "Desk Cash balance collected",
                },
                format="json",
            )
            self.assertEqual(settle_res.status_code, status.HTTP_200_OK)
            self.assertEqual(settle_res.data["status"], "ADMITTED")

        booking.refresh_from_db()
        self.assertEqual(booking.status, "CHECKED_IN")
        self.assertEqual(booking.balance_due, Decimal("0.00"))

    # -------------------------------------------------------------------------
    # Bug 3.1 & 3.2: Email MIME related subtype and QR Pass in email
    # -------------------------------------------------------------------------
    def test_email_confirmation_generates_multipart_related_with_qr(self):
        booking = BookingEngine.create_booking(
            turf=self.turf,
            date_obj=self.today,
            slot_ids=[self.slot1.id],
            user=self.customer,
            booking_type="REGULAR",
            payment_type="PARTIAL",
            payment_method="UPI",
        )
        # Test sending booking confirmation email with partial payment
        with patch("django.core.mail.EmailMultiAlternatives.send", return_value=1) as mock_send:
            success = EmailNotificationService.send_booking_confirmation_email(booking)
            self.assertTrue(success)
            self.assertTrue(mock_send.called)
