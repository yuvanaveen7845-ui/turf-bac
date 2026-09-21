import csv
from decimal import Decimal
from datetime import datetime, date, time, timedelta

from django.test import TestCase
from django.utils import timezone
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient
from rest_framework import status

from accounts.models import User
from turfs.models import Turf, TimeSlot
from bookings.models import Booking
from payments.models import Payment, Refund
from qr_system.models import QRCredential, CheckIn
from maintenance.models import Maintenance
from audit.models import AuditLog

User = get_user_model()


class OperationsControlCenterForensicTests(TestCase):
    def setUp(self):
        self.client = APIClient()

        # Users
        self.admin = User.objects.create_user(
            email="admin_ops@friendsturf.com",
            password="AdminPassword123!",
            role="ADMIN",
            first_name="Admin",
            last_name="Super",
            status="ACTIVE",
        )
        self.staff = User.objects.create_user(
            email="staff_ops@friendsturf.com",
            password="StaffPassword123!",
            role="STAFF",
            first_name="Staff",
            last_name="FrontDesk",
            status="ACTIVE",
        )
        self.customer = User.objects.create_user(
            email="customer_ops@friendsturf.com",
            password="CustomerPassword123!",
            role="CUSTOMER",
            first_name="Player",
            last_name="One",
            status="ACTIVE",
        )

        # Facilities
        self.turf1 = Turf.objects.create(
            name="Main Football Arena A",
            slug="main-football-arena-a",
            sport_type="FOOTBALL",
            base_price=Decimal("1600.00"),
            capacity=14,
            is_active=True,
        )
        self.turf2 = Turf.objects.create(
            name="Box Cricket Pitch B",
            slug="box-cricket-pitch-b",
            sport_type="CRICKET",
            base_price=Decimal("1200.00"),
            capacity=12,
            is_active=True,
        )

        self.today = timezone.now().date()
        self.now_time = timezone.now().time()

        # Slots
        self.slot1 = TimeSlot.objects.create(
            turf=self.turf1,
            date=self.today,
            start_time=time(10, 0),
            end_time=time(11, 0),
            price=Decimal("1600.00"),
            status="AVAILABLE",
        )
        self.slot_expired_hold = TimeSlot.objects.create(
            turf=self.turf1,
            date=self.today,
            start_time=time(11, 0),
            end_time=time(12, 0),
            price=Decimal("1600.00"),
            status="LOCKED",
            locked_by=self.customer,
            locked_until=timezone.now() - timedelta(minutes=10),
        )

    # =========================================================================
    # 1. AUTH & PERMISSIONS
    # =========================================================================
    def test_overview_unauthenticated_returns_401(self):
        res = self.client.get("/api/reports/operations/overview/")
        self.assertEqual(res.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_overview_customer_returns_403(self):
        self.client.force_authenticate(user=self.customer)
        res = self.client.get("/api/reports/operations/overview/")
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)

    def test_overview_staff_and_admin_returns_200(self):
        for user in [self.staff, self.admin]:
            self.client.force_authenticate(user=user)
            res = self.client.get("/api/reports/operations/overview/")
            self.assertEqual(res.status_code, status.HTTP_200_OK)
            self.assertIn("summary", res.data)
            self.assertIn("critical", res.data)
            self.assertIn("attention", res.data)
            self.assertIn("upcoming", res.data)
            self.assertIn("today", res.data)
            self.assertIn("meta", res.data)

    # =========================================================================
    # 2. 🔴 CRITICAL EXCEPTIONS BREAK-TESTS
    # =========================================================================
    def test_critical_payment_mismatch_detected(self):
        """Scenario A: Payment is marked SUCCESSFUL/PAID, but booking remains PAYMENT_PENDING."""
        booking = Booking.objects.create(
            booking_id="FT-26-UNCONF01",
            customer=self.customer,
            turf=self.turf1,
            date=self.today,
            start_time=time(14, 0),
            end_time=time(15, 0),
            status="PAYMENT_PENDING",
            total_amount=Decimal("1600.00"),
            final_amount=Decimal("1600.00"),
            amount_paid=Decimal("0.00"),
            balance_due=Decimal("1600.00"),
        )
        Payment.objects.create(
            payment_id="PAY-MISMATCH-01",
            booking=booking,
            customer=self.customer,
            amount=Decimal("1600.00"),
            status="PAID",
            provider="RAZORPAY",
            provider_payment_id="pay_mismatch_rzp_01",
            transaction_reference="TXN-MISMATCH-01",
        )

        self.client.force_authenticate(user=self.admin)
        res = self.client.get("/api/reports/operations/overview/")
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        mismatches = res.data["critical"]["payment_mismatches"]
        matching = [m for m in mismatches if m.get("booking_id") == "FT-26-UNCONF01"]
        self.assertTrue(len(matching) > 0, "Unconfirmed paid booking must appear in payment mismatches.")

    def test_critical_failed_refunds_detected(self):
        """Failed refund records appear with correct details in Critical tier."""
        booking = Booking.objects.create(
            booking_id="FT-26-REFUND01",
            customer=self.customer,
            turf=self.turf1,
            date=self.today,
            start_time=time(16, 0),
            end_time=time(17, 0),
            status="CANCELLED",
            total_amount=Decimal("1600.00"),
            final_amount=Decimal("1600.00"),
            amount_paid=Decimal("1600.00"),
            balance_due=Decimal("0.00"),
        )
        payment = Payment.objects.create(
            payment_id="PAY-REF-01",
            booking=booking,
            customer=self.customer,
            amount=Decimal("1600.00"),
            status="PAID",
            provider="RAZORPAY",
            transaction_reference="TXN-REF-01",
        )
        Refund.objects.create(
            refund_id="RF-ERR-001",
            payment=payment,
            booking=booking,
            amount=Decimal("1600.00"),
            refund_to="ORIGINAL_PAYMENT_SOURCE",
            status="FAILED",
            reason="Bank gateway timeout during reverse transfer",
        )

        self.client.force_authenticate(user=self.admin)
        res = self.client.get("/api/reports/operations/overview/")
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        failed_refunds = res.data["critical"]["failed_refunds"]
        matching = [r for r in failed_refunds if r["refund_id"] == "RF-ERR-001"]
        self.assertEqual(len(matching), 1)
        self.assertEqual(matching[0]["amount"], 1600.0)
        self.assertIn("timeout", matching[0]["reason"])

    def test_critical_booking_conflicts_cancelled_holding_slots(self):
        """Cancelled bookings that retain BOOKED slots are surfaced as conflicts."""
        booking = Booking.objects.create(
            booking_id="FT-26-STUCK01",
            customer=self.customer,
            turf=self.turf1,
            date=self.today,
            start_time=time(18, 0),
            end_time=time(19, 0),
            status="CANCELLED",
            total_amount=Decimal("1600.00"),
            final_amount=Decimal("1600.00"),
            amount_paid=Decimal("0.00"),
            balance_due=Decimal("0.00"),
        )
        stuck_slot = TimeSlot.objects.create(
            turf=self.turf1,
            date=self.today,
            start_time=time(18, 0),
            end_time=time(19, 0),
            price=Decimal("1600.00"),
            status="BOOKED",
        )
        booking.slots.add(stuck_slot)

        self.client.force_authenticate(user=self.admin)
        res = self.client.get("/api/reports/operations/overview/")
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        conflicts = res.data["critical"]["booking_conflicts"]
        matching = [c for c in conflicts if c.get("booking_id") == "FT-26-STUCK01"]
        self.assertEqual(len(matching), 1)
        self.assertEqual(matching[0]["conflict_type"], "CANCELLED_HOLDING_SLOT")

    # =========================================================================
    # 3. 🟠 ATTENTION TIER BREAK-TESTS
    # =========================================================================
    def test_attention_expired_holds_and_safe_release(self):
        """Expired slot holds appear in Attention and can be atomically released."""
        self.client.force_authenticate(user=self.admin)
        res = self.client.get("/api/reports/operations/overview/")
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        holds = res.data["attention"]["expired_holds"]
        slot_ids = [h["slot_id"] for h in holds]
        self.assertIn(self.slot_expired_hold.id, slot_ids)

        # Release single hold
        release_res = self.client.post("/api/reports/operations/release-hold/", {"slot_id": self.slot_expired_hold.id})
        self.assertEqual(release_res.status_code, status.HTTP_200_OK)
        self.assertTrue(release_res.data["success"])

        self.slot_expired_hold.refresh_from_db()
        self.assertEqual(self.slot_expired_hold.status, "AVAILABLE")
        self.assertIsNone(self.slot_expired_hold.locked_until)

        # Audit event checked
        audit = AuditLog.objects.filter(action="OPERATIONS_HOLD_RELEASED", user=self.admin).first()
        self.assertIsNotNone(audit)

    def test_attention_partial_payment_balance_due_today(self):
        """Bookings scheduled today with outstanding balance appear in Attention."""
        booking = Booking.objects.create(
            booking_id="FT-26-PARTIAL01",
            customer=self.customer,
            turf=self.turf1,
            date=self.today,
            start_time=time(19, 0),
            end_time=time(20, 0),
            status="CONFIRMED",
            total_amount=Decimal("1600.00"),
            final_amount=Decimal("1600.00"),
            amount_paid=Decimal("800.00"),
            balance_due=Decimal("800.00"),
        )

        self.client.force_authenticate(user=self.staff)
        res = self.client.get("/api/reports/operations/overview/")
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        pending = res.data["attention"]["pending_payments"]
        matching = [p for p in pending if p["booking_id"] == "FT-26-PARTIAL01"]
        self.assertEqual(len(matching), 1)
        self.assertEqual(matching[0]["balance_due"], 800.0)
        self.assertEqual(matching[0]["amount_paid"], 800.0)

    def test_attention_denied_turnstile_checkins(self):
        """Denied turnstile check-ins appear with reason codes and device info."""
        booking = Booking.objects.create(
            booking_id="FT-26-GATE01",
            customer=self.customer,
            turf=self.turf1,
            date=self.today,
            start_time=time(20, 0),
            end_time=time(21, 0),
            status="CONFIRMED",
            total_amount=Decimal("1600.00"),
            final_amount=Decimal("1600.00"),
            amount_paid=Decimal("1600.00"),
            balance_due=Decimal("0.00"),
        )
        CheckIn.objects.create(
            booking=booking,
            staff_user=self.staff,
            turf=self.turf1,
            method="QR_SCAN",
            decision="DENY",
            reason_code="OUTSIDE_ENTRY_WINDOW",
            message="Kickoff is in 3 hours. Gate admissions open 15 min prior.",
            device_identifier="TURNSTILE_NORTH_01",
        )

        self.client.force_authenticate(user=self.staff)
        res = self.client.get("/api/reports/operations/overview/")
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        checkins = res.data["attention"]["failed_checkins"]
        matching = [c for c in checkins if c["booking_id"] == "FT-26-GATE01"]
        self.assertEqual(len(matching), 1)
        self.assertEqual(matching[0]["reason_code"], "OUTSIDE_ENTRY_WINDOW")
        self.assertEqual(matching[0]["device"], "TURNSTILE_NORTH_01")

    # =========================================================================
    # 4. 🟡 UPCOMING TIER BREAK-TESTS
    # =========================================================================
    def test_upcoming_maintenance_with_conflict_detection(self):
        """Maintenance overlapping confirmed bookings surfaces conflict counts."""
        # 1. Create confirmed booking 13:00 - 15:00
        Booking.objects.create(
            booking_id="FT-26-OVERLAP01",
            customer=self.customer,
            turf=self.turf2,
            date=self.today,
            start_time=time(13, 0),
            end_time=time(15, 0),
            status="CONFIRMED",
            total_amount=Decimal("2400.00"),
            final_amount=Decimal("2400.00"),
            amount_paid=Decimal("2400.00"),
            balance_due=Decimal("0.00"),
        )
        # 2. Create maintenance 14:00 - 16:00
        Maintenance.objects.create(
            turf=self.turf2,
            date=self.today,
            start_time=time(14, 0),
            end_time=time(16, 0),
            reason="Floodlight bulb replacement",
            assigned_staff=self.staff,
            status="SCHEDULED",
        )

        self.client.force_authenticate(user=self.admin)
        res = self.client.get("/api/reports/operations/overview/")
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        maint_list = res.data["upcoming"]["maintenance"]
        matching = [m for m in maint_list if m["turf_name"] == "Box Cricket Pitch B"]
        self.assertEqual(len(matching), 1)
        self.assertEqual(matching[0]["conflicts_count"], 1, "Conflict with booking FT-26-OVERLAP01 must be detected.")

    def test_upcoming_no_shows_detection_and_marking(self):
        """Past kickoff match without attendance appears in no-shows and can be transitioned."""
        # Elapsed booking
        booking = Booking.objects.create(
            booking_id="FT-26-ELAPSED01",
            customer=self.customer,
            turf=self.turf1,
            date=self.today,
            start_time=time(6, 0),
            end_time=time(7, 0),
            status="CONFIRMED",
            total_amount=Decimal("1600.00"),
            final_amount=Decimal("1600.00"),
            amount_paid=Decimal("1600.00"),
            balance_due=Decimal("0.00"),
        )

        self.client.force_authenticate(user=self.staff)
        res = self.client.get("/api/reports/operations/overview/")
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        no_shows = res.data["upcoming"]["no_shows"]
        matching = [n for n in no_shows if n["booking_id"] == "FT-26-ELAPSED01"]
        self.assertEqual(len(matching), 1)

        # Mark no-show
        action_res = self.client.post("/api/reports/operations/mark-no-show/", {"booking_id": "FT-26-ELAPSED01"})
        self.assertEqual(action_res.status_code, status.HTTP_200_OK)
        booking.refresh_from_db()
        self.assertEqual(booking.status, "NO_SHOW")

    # =========================================================================
    # 5. 🟢 TODAY'S REVENUE, OCCUPANCY & FINANCIAL INVARIANTS
    # =========================================================================
    def test_today_revenue_and_financial_invariants(self):
        """Verified payments, drawer cash, and refunds compute exact net revenue."""
        b1 = Booking.objects.create(
            booking_id="FT-26-REV01",
            customer=self.customer,
            turf=self.turf1,
            date=self.today,
            start_time=time(12, 0),
            end_time=time(13, 0),
            status="CONFIRMED",
            total_amount=Decimal("1600.00"),
            final_amount=Decimal("1600.00"),
            amount_paid=Decimal("1600.00"),
            balance_due=Decimal("0.00"),
        )
        p1 = Payment.objects.create(
            payment_id="PAY-REV-01",
            booking=b1,
            customer=self.customer,
            amount=Decimal("1600.00"),
            status="PAID",
            provider="RAZORPAY",
            transaction_reference="TXN-REV-01",
        )

        b2 = Booking.objects.create(
            booking_id="FT-26-REV02",
            customer=self.customer,
            turf=self.turf2,
            date=self.today,
            start_time=time(13, 0),
            end_time=time(14, 0),
            status="CONFIRMED",
            booking_type="WALK_IN",
            total_amount=Decimal("1200.00"),
            final_amount=Decimal("1200.00"),
            amount_paid=Decimal("1200.00"),
            balance_due=Decimal("0.00"),
        )
        p2 = Payment.objects.create(
            payment_id="PAY-REV-02",
            booking=b2,
            customer=self.customer,
            amount=Decimal("1200.00"),
            status="PAID",
            provider="CASH",
            transaction_reference="TXN-REV-02",
        )

        # Process a refund of ₹400
        Refund.objects.create(
            refund_id="RF-SUCC-01",
            payment=p1,
            booking=b1,
            amount=Decimal("400.00"),
            status="COMPLETED",
        )

        self.client.force_authenticate(user=self.admin)
        res = self.client.get("/api/reports/operations/overview/")
        self.assertEqual(res.status_code, status.HTTP_200_OK)

        drawer = res.data["today"]["financial_drawer"]
        gross = drawer["gross_revenue"]
        online = drawer["online_revenue"]
        cash = drawer["cash_revenue"]
        refunds = drawer["refunds_total"]
        net = drawer["net_revenue"]

        self.assertEqual(online, 1600.0)
        self.assertEqual(cash, 1200.0)
        self.assertEqual(gross, 2800.0)
        self.assertEqual(refunds, 400.0)
        self.assertEqual(net, 2400.0)
        self.assertEqual(net, gross - refunds, "Financial Invariant: Net Revenue == Gross Collected - Refunds")

    # =========================================================================
    # 6. REPORTS CSV EXPORT REGRESSION BREAK-TESTS
    # =========================================================================
    def test_reports_csv_all_types_with_edge_cases(self):
        """Tests Revenue, Utilization, and Bookings CSV exports with guest customer & discounts."""
        b_guest = Booking.objects.create(
            booking_id="FT-26-GUEST01",
            customer=self.customer,
            turf=self.turf1,
            date=self.today,
            start_time=time(15, 0),
            end_time=time(16, 0),
            status="CONFIRMED",
            total_amount=Decimal("1600.00"),
            discount_amount=Decimal("200.00"),
            final_amount=Decimal("1400.00"),
            amount_paid=Decimal("700.00"),
            balance_due=Decimal("700.00"),
        )

        self.client.force_authenticate(user=self.admin)

        # 1. Revenue CSV
        res_rev = self.client.get("/api/reports/export-csv/?type=revenue")
        self.assertEqual(res_rev.status_code, status.HTTP_200_OK)
        self.assertEqual(res_rev["Content-Type"], "text/csv")
        self.assertIn("friends_turf_revenue_report.csv", res_rev["Content-Disposition"])
        content_rev = res_rev.content.decode("utf-8")
        self.assertIn("FT-26-GUEST01", content_rev)
        self.assertIn("1400.0", content_rev)
        self.assertIn("700.0", content_rev)

        # 2. Utilization CSV
        res_util = self.client.get("/api/reports/export-csv/?type=utilization")
        self.assertEqual(res_util.status_code, status.HTTP_200_OK)
        self.assertEqual(res_util["Content-Type"], "text/csv")
        content_util = res_util.content.decode("utf-8")
        self.assertIn("Main Football Arena A", content_util)
        self.assertIn("Box Cricket Pitch B", content_util)

        # 3. Bookings CSV
        res_book = self.client.get("/api/reports/export-csv/?type=bookings")
        self.assertEqual(res_book.status_code, status.HTTP_200_OK)
        self.assertEqual(res_book["Content-Type"], "text/csv")
        content_book = res_book.content.decode("utf-8")
        self.assertIn("FT-26-GUEST01", content_book)
