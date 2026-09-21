from datetime import date, time, timedelta
from decimal import Decimal
from django.test import TestCase
from django.utils import timezone
from accounts.models import User
from turfs.models import Turf, TimeSlot, Facility
from turfs.services import SchedulingEngine
from maintenance.models import Maintenance


class SchedulingEngineTests(TestCase):
    def setUp(self):
        self.turf = Turf.objects.create(
            name="Main FIFA Pitch",
            slug="main-fifa-pitch",
            sport_type="FOOTBALL",
            description="50mm FIFA standard artificial pitch",
            location="Koramangala, Bangalore",
            address="100ft Road",
            base_price=Decimal("1200.00"),
            operating_hours_start=time(6, 0),
            operating_hours_end=time(23, 0),
            slot_duration_minutes=60,
        )
        self.user = User.objects.create_user(
            email="customer@friendsturf.local", password="password123", role="CUSTOMER"
        )
        self.today = timezone.now().date()

    def test_generate_daily_slots_respects_operating_hours(self):
        """Slots should be generated from 06:00 to 23:00 (17 1-hour slots)."""
        slots = SchedulingEngine.generate_daily_slots(self.turf, self.today)
        self.assertEqual(len(slots), 17)
        self.assertEqual(slots[0].start_time, time(6, 0))
        self.assertEqual(slots[-1].end_time, time(23, 0))
        self.assertEqual(slots[0].status, "AVAILABLE")

    def test_cleanup_expired_locks(self):
        """Expired slot locks should be automatically returned to AVAILABLE."""
        slot = TimeSlot.objects.create(
            turf=self.turf,
            date=self.today,
            start_time=time(18, 0),
            end_time=time(19, 0),
            status="LOCKED",
            locked_until=timezone.now() - timedelta(minutes=1),
            locked_by=self.user,
        )
        count = SchedulingEngine.cleanup_expired_locks(self.turf, self.today)
        self.assertEqual(count, 1)

        slot.refresh_from_db()
        self.assertEqual(slot.status, "AVAILABLE")
        self.assertIsNone(slot.locked_until)
        self.assertIsNone(slot.locked_by)

    def test_maintenance_blackout_updates_slot_availability(self):
        """Slots overlapping maintenance schedules should be marked as MAINTENANCE."""
        Maintenance.objects.create(
            turf=self.turf,
            date=self.today,
            start_time=time(14, 0),
            end_time=time(16, 0),
            reason="Turf grooming and floodlight maintenance",
            status="SCHEDULED",
        )
        avail = SchedulingEngine.get_turf_availability(self.turf, self.today)
        maint_slots = [
            s for s in avail["slots"] if s["status"] == "MAINTENANCE"
        ]
        self.assertGreaterEqual(len(maint_slots), 2)
        # Verify 14:00-15:00 and 15:00-16:00 are not available
        for s in maint_slots:
            self.assertFalse(s["is_available"])


class TurfAPITests(TestCase):
    def setUp(self):
        self.admin_user = User.objects.create_superuser(
            email="admin@friendsturf.local", password="adminpassword", role="ADMIN"
        )
        self.customer_user = User.objects.create_user(
            email="cust@friendsturf.local", password="password123", role="CUSTOMER"
        )
        self.turf = Turf.objects.create(
            name="Deletable Arena",
            slug="deletable-arena",
            sport_type="FOOTBALL",
            description="Test arena",
            location="Tiruppur",
            address="Friends Turf",
            base_price=Decimal("1000.00"),
            operating_hours_start=time(6, 0),
            operating_hours_end=time(23, 0),
        )

    def test_admin_can_delete_turf(self):
        from rest_framework.test import APIClient
        client = APIClient()
        client.force_authenticate(user=self.admin_user)
        response = client.delete(f"/api/turfs/{self.turf.id}/")
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Turf.objects.filter(id=self.turf.id).exists())

    def test_customer_cannot_delete_turf(self):
        from rest_framework.test import APIClient
        client = APIClient()
        client.force_authenticate(user=self.customer_user)
        response = client.delete(f"/api/turfs/{self.turf.id}/")
        self.assertEqual(response.status_code, 403)
        self.assertTrue(Turf.objects.filter(id=self.turf.id).exists())


