from datetime import datetime, date, time, timedelta
from decimal import Decimal
from django.test import TestCase
from django.contrib.auth import get_user_model
from django.utils import timezone
from rest_framework.test import APIClient

from turfs.models import Turf, TimeSlot
from bookings.models import Booking
from qr_system.models import QRCredential, CheckIn
from qr_system.services import QRService

User = get_user_model()


class QRSystemTestCase(TestCase):
    def setUp(self):
        self.client = APIClient()

        # Users
        self.customer = User.objects.create_user(
            email="customer@example.com",
            password="Password123!",
            first_name="Virat",
            last_name="Kohli",
            role="CUSTOMER",
        )
        self.staff_user = User.objects.create_user(
            email="staff@example.com",
            password="Password123!",
            first_name="Ramesh",
            last_name="Staff",
            role="STAFF",
        )
        self.admin_user = User.objects.create_user(
            email="admin@example.com",
            password="Password123!",
            first_name="Admin",
            last_name="FriendsTurf",
            role="ADMIN",
        )

        # Turf
        self.turf = Turf.objects.create(
            name="Main FIFA Pitch 1",
            slug="main-fifa-pitch-1",
            sport_type="FOOTBALL",
            base_price=Decimal("1500.00"),
            location="Indiranagar, Bengaluru",
            address="100 Feet Rd, Indiranagar, Bengaluru",
            description="Premium FIFA Certified Arena",
            capacity=14,
            surface_spec="50mm Monofilament FIFA Turf",
        )

        # TimeSlots
        self.today = timezone.localdate()
        self.slot = TimeSlot.objects.create(
            turf=self.turf,
            date=self.today,
            start_time=time(19, 0),
            end_time=time(20, 0),
            price=Decimal("1500.00"),
            status="BOOKED",
        )

        # Today's active booking
        self.booking = Booking.objects.create(
            booking_id="FT-20260915-TEST1",
            customer=self.customer,
            turf=self.turf,
            date=self.today,
            start_time=time(19, 0),
            end_time=time(20, 0),
            status="CONFIRMED",
            total_amount=Decimal("1500.00"),
            final_amount=Decimal("1500.00"),
            amount_paid=Decimal("1500.00"),
            balance_due=Decimal("0.00"),
        )
        self.booking.slots.add(self.slot)

    def test_qr_generation(self):
        """Test cryptographic credential issuance with Error Correction H"""
        cred = QRService.generate_credential_for_booking(self.booking)
        self.assertIsNotNone(cred)
        self.assertTrue(cred.credential_token.startswith("FT-PASS-"))
        self.assertEqual(len(cred.credential_hash), 64)
        self.assertEqual(cred.status, "ACTIVE")
        self.assertTrue(cred.qr_base64.startswith("data:image/png;base64,"))
        self.assertEqual(cred.credential_version, 1)

    def test_evaluate_and_checkin_success(self):
        """Test valid on-time check-in by staff"""
        cred = QRService.generate_credential_for_booking(self.booking)

        # Call service check-in
        result = QRService.evaluate_and_checkin(
            raw_input=cred.credential_token,
            staff_user=self.staff_user,
            method="QR_SCAN",
            is_override=True,  # Override time for deterministic test execution
        )

        self.assertTrue(result["valid"])
        self.assertEqual(result["decision"], "ALLOW")

        # Reload booking and check status
        self.booking.refresh_from_db()
        self.assertEqual(self.booking.status, "CHECKED_IN")
        self.assertIsNotNone(self.booking.checked_in_at)
        self.assertEqual(self.booking.checked_in_by, self.staff_user)

        cred.refresh_from_db()
        self.assertEqual(cred.status, "USED")
        self.assertEqual(cred.scan_count, 1)

    def test_duplicate_checkin_blocked(self):
        """Test idempotency: second scan of already admitted pass is denied"""
        cred = QRService.generate_credential_for_booking(self.booking)

        # 1st scan -> Approved
        res1 = QRService.evaluate_and_checkin(
            raw_input=cred.credential_token,
            staff_user=self.staff_user,
            method="QR_SCAN",
            is_override=True,
        )
        self.assertTrue(res1["valid"])

        # 2nd scan -> Blocked
        res2 = QRService.evaluate_and_checkin(
            raw_input=cred.credential_token,
            staff_user=self.staff_user,
            method="QR_SCAN",
            is_override=True,
        )
        self.assertFalse(res2["valid"])
        self.assertEqual(res2["decision"], "DENY")
        self.assertEqual(res2["reason_code"], "ALREADY_CHECKED_IN")

    def test_cancelled_booking_scan_denied(self):
        """Test scanning a cancelled booking is denied"""
        self.booking.status = "CANCELLED"
        self.booking.save()

        cred = QRService.generate_credential_for_booking(self.booking)
        result = QRService.evaluate_and_checkin(
            raw_input=cred.credential_token,
            staff_user=self.staff_user,
            method="QR_SCAN",
        )
        self.assertFalse(result["valid"])
        self.assertEqual(result["decision"], "DENY")
        self.assertEqual(result["reason_code"], "BOOKING_CANCELLED")

    def test_revoked_credential_denied(self):
        """Test revoking a pass blocks gate entry"""
        cred = QRService.generate_credential_for_booking(self.booking)
        QRService.revoke_credential(self.booking, reason="Fraud check", user=self.admin_user)

        result = QRService.evaluate_and_checkin(
            raw_input=cred.credential_token,
            staff_user=self.staff_user,
            method="QR_SCAN",
        )
        self.assertFalse(result["valid"])
        self.assertEqual(result["decision"], "DENY")
        self.assertEqual(result["reason_code"], "CREDENTIAL_REVOKED")

    def test_manual_override_flow(self):
        """Test manual override by staff/admin with reason"""
        cred = QRService.generate_credential_for_booking(self.booking)

        result = QRService.evaluate_and_checkin(
            raw_input=self.booking.booking_id,
            staff_user=self.admin_user,
            method="ADMIN_OVERRIDE",
            override_reason="VIP guest authorized by management",
            is_override=True,
        )
        self.assertTrue(result["valid"])
        self.assertEqual(result["reason_code"], "MANUAL_OVERRIDE")

        # Verify audit check-in log
        log = CheckIn.objects.filter(booking=self.booking, method="ADMIN_OVERRIDE").first()
        self.assertIsNotNone(log)
        self.assertEqual(log.override_reason, "VIP guest authorized by management")

    def test_api_staff_scan_endpoint(self):
        """Test POST /api/qr/scan/ API with authenticated staff"""
        cred = QRService.generate_credential_for_booking(self.booking)
        self.client.force_authenticate(user=self.staff_user)

        response = self.client.post("/api/qr/scan/", {"qr_data": cred.credential_token})
        self.assertEqual(response.status_code, 200)

    def test_api_customer_cannot_call_scan(self):
        """Test customer forbidden from calling staff gate scan endpoint"""
        cred = QRService.generate_credential_for_booking(self.booking)
        self.client.force_authenticate(user=self.customer)

        response = self.client.post("/api/qr/scan/", {"qr_data": cred.credential_token})
        self.assertEqual(response.status_code, 403)
