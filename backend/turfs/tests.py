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

    def test_admin_can_soft_delete_turf(self):
        from rest_framework.test import APIClient
        client = APIClient()
        client.force_authenticate(user=self.admin_user)

        # Pre-generate future slots for this turf
        SchedulingEngine.generate_daily_slots(self.turf, timezone.now().date())
        self.assertGreater(TimeSlot.objects.filter(turf=self.turf).count(), 0)

        response = client.delete(f"/api/turfs/{self.turf.id}/")
        self.assertEqual(response.status_code, 200)

        self.turf.refresh_from_db()
        self.assertTrue(self.turf.is_deleted)
        self.assertFalse(self.turf.is_active)
        self.assertIsNotNone(self.turf.deleted_at)

        # Unbooked future slots should be evicted
        self.assertEqual(
            TimeSlot.objects.filter(
                turf=self.turf,
                date__gte=timezone.now().date(),
                status__in=["AVAILABLE", "LOCKED"],
            ).count(),
            0,
        )

        # Omitted from general list view
        list_res = client.get("/api/turfs/")
        turf_ids = [t["id"] for t in list_res.data]
        self.assertNotIn(self.turf.id, turf_ids)

    def test_admin_can_delete_turf_with_active_bookings_auto_cancels(self):
        """Admin should be able to delete turf even with active/mock bookings; bookings are auto-cancelled."""
        from rest_framework.test import APIClient
        from bookings.models import Booking

        client = APIClient()
        client.force_authenticate(user=self.admin_user)

        today = timezone.now().date()
        booking = Booking.objects.create(
            booking_id="FT-TEST-ACTIVE",
            customer=self.customer_user,
            turf=self.turf,
            date=today,
            start_time=time(18, 0),
            end_time=time(19, 0),
            status="CONFIRMED",
            total_amount=Decimal("1000.00"),
            final_amount=Decimal("1000.00"),
            amount_paid=Decimal("1000.00"),
        )

        response = client.delete(f"/api/turfs/{self.turf.id}/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["cancelled_booking_count"], 1)

        self.turf.refresh_from_db()
        self.assertTrue(self.turf.is_deleted)
        self.assertFalse(self.turf.is_active)

        # Booking is safely cancelled rather than blocking deletion
        booking.refresh_from_db()
        self.assertEqual(booking.status, "CANCELLED")
        self.assertIn("decommissioned", booking.cancel_reason)

    def test_can_delete_turf_with_historical_completed_bookings_and_reviews(self):
        """Soft-deleting a turf with past bookings/reviews should not raise ProtectedError."""
        from rest_framework.test import APIClient
        from bookings.models import Booking
        from reviews.models import Review

        client = APIClient()
        client.force_authenticate(user=self.admin_user)

        past_date = timezone.now().date() - timedelta(days=5)
        past_booking = Booking.objects.create(
            booking_id="FT-TEST-PAST",
            customer=self.customer_user,
            turf=self.turf,
            date=past_date,
            start_time=time(18, 0),
            end_time=time(19, 0),
            status="COMPLETED",
            total_amount=Decimal("1000.00"),
            final_amount=Decimal("1000.00"),
            amount_paid=Decimal("1000.00"),
        )
        Review.objects.create(
            booking=past_booking,
            customer=self.customer_user,
            turf=self.turf,
            rating=5,
            review_text="Historic great venue",
        )

        # Deleting must succeed and soft-delete instead of crashing with ProtectedError
        response = client.delete(f"/api/turfs/{self.turf.id}/")
        self.assertEqual(response.status_code, 200)

        self.turf.refresh_from_db()
        self.assertTrue(self.turf.is_deleted)
        # Historical booking and review remain preserved
        self.assertTrue(Booking.objects.filter(booking_id="FT-TEST-PAST").exists())
        self.assertTrue(Review.objects.filter(booking=past_booking).exists())

    def test_customer_cannot_delete_turf(self):
        from rest_framework.test import APIClient
        client = APIClient()
        client.force_authenticate(user=self.customer_user)
        response = client.delete(f"/api/turfs/{self.turf.id}/")
        self.assertEqual(response.status_code, 403)
        self.turf.refresh_from_db()
        self.assertFalse(self.turf.is_deleted)

    def test_edit_operating_hours_realigns_future_unbooked_slots(self):
        """Updating operating hours should trim unbooked slots outside the new window."""
        from rest_framework.test import APIClient
        client = APIClient()
        client.force_authenticate(user=self.admin_user)

        tomorrow = timezone.now().date() + timedelta(days=1)
        # Initially 06:00 to 23:00 -> 17 slots
        SchedulingEngine.generate_daily_slots(self.turf, tomorrow)
        initial_count = TimeSlot.objects.filter(turf=self.turf, date=tomorrow).count()
        self.assertEqual(initial_count, 17)

        # Update operating hours to 08:00 - 20:00 (12 slots)
        response = client.put(
            f"/api/turfs/{self.turf.id}/",
            {"operating_hours_start": "08:00:00", "operating_hours_end": "20:00:00"},
            format="json",
        )
        self.assertEqual(response.status_code, 200)

        slots_after = list(TimeSlot.objects.filter(turf=self.turf, date=tomorrow).order_by("start_time"))
        self.assertEqual(len(slots_after), 12)
        self.assertEqual(slots_after[0].start_time, time(8, 0))
        self.assertEqual(slots_after[-1].end_time, time(20, 0))

    def test_edit_base_price_updates_future_unbooked_slots(self):
        """Updating base price should reflect across future unbooked available slots."""
        from rest_framework.test import APIClient
        client = APIClient()
        client.force_authenticate(user=self.admin_user)

        tomorrow = timezone.now().date() + timedelta(days=2)
        SchedulingEngine.generate_daily_slots(self.turf, tomorrow)

        # Update base price from 1000 to 1500
        response = client.put(
            f"/api/turfs/{self.turf.id}/",
            {"base_price": "1500.00"},
            format="json",
        )
        self.assertEqual(response.status_code, 200)

        avail_prices = set(
            TimeSlot.objects.filter(turf=self.turf, date=tomorrow, status="AVAILABLE").values_list("price", flat=True)
        )
        self.assertEqual(avail_prices, {Decimal("1500.00")})

    def test_edit_turf_preserves_confirmed_bookings_during_realign(self):
        """Re-aligning operating hours must never delete or modify confirmed customer match slots."""
        from rest_framework.test import APIClient
        from bookings.models import Booking
        client = APIClient()
        client.force_authenticate(user=self.admin_user)

        tomorrow = timezone.now().date() + timedelta(days=1)
        slots = SchedulingEngine.generate_daily_slots(self.turf, tomorrow)
        booked_slot = slots[10]  # e.g., 16:00-17:00
        booked_slot.status = "BOOKED"
        booked_slot.save()

        booking = Booking.objects.create(
            booking_id="FT-REPAIR-SAFE",
            customer=self.customer_user,
            turf=self.turf,
            date=tomorrow,
            start_time=booked_slot.start_time,
            end_time=booked_slot.end_time,
            status="CONFIRMED",
            total_amount=Decimal("1000.00"),
            final_amount=Decimal("1000.00"),
            amount_paid=Decimal("1000.00"),
        )
        booking.slots.add(booked_slot)

        # Update hours to close earlier at 19:00
        response = client.put(
            f"/api/turfs/{self.turf.id}/",
            {"operating_hours_end": "19:00:00"},
            format="json",
        )
        self.assertEqual(response.status_code, 200)

        # Booked slot must still exist with status BOOKED
        booked_slot.refresh_from_db()
        self.assertEqual(booked_slot.status, "BOOKED")
        self.assertTrue(Booking.objects.filter(booking_id="FT-REPAIR-SAFE").exists())

    def test_create_turf_pre_generates_initial_daily_slots(self):
        """Creating a new arena should immediately generate initial daily slots for immediate booking."""
        from rest_framework.test import APIClient
        client = APIClient()
        client.force_authenticate(user=self.admin_user)

        payload = {
            "name": "Emerald Grand Dome",
            "slug": "emerald-grand-dome",
            "sport_type": "FOOTBALL",
            "description": "New tournament dome",
            "location": "Tiruppur",
            "address": "Near Ring Road",
            "base_price": "1800.00",
            "operating_hours_start": "06:00:00",
            "operating_hours_end": "22:00:00",
            "slot_duration_minutes": 60,
        }
        response = client.post("/api/turfs/", payload, format="json")
        self.assertEqual(response.status_code, 201)

        new_turf_id = response.data["id"]
        tomorrow = timezone.now().date() + timedelta(days=1)
        slots_count = TimeSlot.objects.filter(turf_id=new_turf_id, date=tomorrow).count()
        # 06:00 to 22:00 is 16 slots
        self.assertEqual(slots_count, 16)



