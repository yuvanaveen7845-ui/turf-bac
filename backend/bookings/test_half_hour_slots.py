from datetime import date, time, timedelta
from decimal import Decimal
from django.test import TestCase
from django.utils import timezone
from accounts.models import User
from turfs.models import Turf, TimeSlot
from turfs.services import SchedulingEngine
from bookings.models import Booking
from bookings.services import BookingEngine
from pricing.engine import PricingEngine


class HalfHourSlotBookingTests(TestCase):
    def setUp(self):
        self.turf_30m = Turf.objects.create(
            name="Half-Hour Multi-Sport Pitch",
            slug="half-hour-pitch",
            sport_type="FOOTBALL",
            description="30-min slot pitch",
            location="Tiruppur",
            address="Kamatchepuram",
            base_price=Decimal("1000.00"),  # 1000 per hour, so 500 per 30 mins
            operating_hours_start=time(6, 0),
            operating_hours_end=time(23, 0),
            slot_duration_minutes=30,
        )
        self.customer = User.objects.create_user(
            email="player_30m@friendsturf.local",
            password="password123",
            role="CUSTOMER",
            first_name="Karthik",
            last_name="Raja",
        )
        self.future_date = timezone.now().date() + timedelta(days=2)
        self.slots = SchedulingEngine.generate_daily_slots(self.turf_30m, self.future_date)

    def test_30min_slot_generation_and_prorated_pricing(self):
        """Slots are generated every 30 minutes with prorated base price (500 INR)."""
        self.assertTrue(len(self.slots) > 0)
        # Verify 30m intervals (e.g. 06:00-06:30, 06:30-07:00)
        slot1 = self.slots[0]
        slot2 = self.slots[1]
        self.assertEqual(slot1.start_time, time(6, 0))
        self.assertEqual(slot1.end_time, time(6, 30))
        self.assertEqual(slot1.price, Decimal("500.00"))

        self.assertEqual(slot2.start_time, time(6, 30))
        self.assertEqual(slot2.end_time, time(7, 0))
        self.assertEqual(slot2.price, Decimal("500.00"))

    def test_single_30min_slot_lock_fails_min_duration_constraint(self):
        """Attempting to lock only one 30-minute slot is rejected (min 60 minutes required)."""
        slot = self.slots[0]  # 06:00 - 06:30 (30 mins)
        success, msg = BookingEngine.lock_slots(
            self.turf_30m, self.future_date, [str(slot.id)], self.customer
        )
        self.assertFalse(success)
        self.assertIn("Minimum match duration is 60 minutes", msg)

    def test_single_30min_slot_create_fails_min_duration_constraint(self):
        """Attempting to create booking for only one 30-minute slot raises ValueError."""
        slot = self.slots[0]
        with self.assertRaises(ValueError) as ctx:
            BookingEngine.create_booking(
                turf=self.turf_30m,
                date_obj=self.future_date,
                slot_ids=[str(slot.id)],
                user=self.customer,
            )
        self.assertIn("Minimum match duration is 60 minutes", str(ctx.exception))

    def test_consecutive_30min_slots_booking_1030_to_1130_success(self):
        """Booking 10:30 to 11:30 (two 30-min slots: 10:30-11:00 and 11:00-11:30) succeeds."""
        # Find 10:30-11:00 and 11:00-11:30 slots
        slot_1030 = TimeSlot.objects.get(
            turf=self.turf_30m, date=self.future_date, start_time=time(10, 30)
        )
        slot_1100 = TimeSlot.objects.get(
            turf=self.turf_30m, date=self.future_date, start_time=time(11, 0)
        )

        slot_ids = [str(slot_1030.id), str(slot_1100.id)]

        # Lock slots
        success, res = BookingEngine.lock_slots(
            self.turf_30m, self.future_date, slot_ids, self.customer
        )
        self.assertTrue(success)

        # Create booking
        booking = BookingEngine.create_booking(
            turf=self.turf_30m,
            date_obj=self.future_date,
            slot_ids=slot_ids,
            user=self.customer,
            payment_type="FULL",
        )

        self.assertIsNotNone(booking)
        self.assertEqual(booking.start_time, time(10, 30))
        self.assertEqual(booking.end_time, time(11, 30))
        # Total price for 1 hour at 1000/hr base rate
        self.assertEqual(booking.final_amount, Decimal("1000.00"))
        self.assertEqual(booking.slots.count(), 2)

    def test_non_contiguous_slots_rejected(self):
        """Attempting to book non-consecutive slots (e.g. 10:30-11:00 and 11:30-12:00) is rejected."""
        slot_1030 = TimeSlot.objects.get(
            turf=self.turf_30m, date=self.future_date, start_time=time(10, 30)
        )
        slot_1130 = TimeSlot.objects.get(
            turf=self.turf_30m, date=self.future_date, start_time=time(11, 30)
        )

        slot_ids = [str(slot_1030.id), str(slot_1130.id)]

        success, msg = BookingEngine.lock_slots(
            self.turf_30m, self.future_date, slot_ids, self.customer
        )
        self.assertFalse(success)
        self.assertIn("not contiguous", msg)
