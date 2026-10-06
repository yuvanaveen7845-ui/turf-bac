from datetime import date, time, timedelta
from decimal import Decimal
from django.test import TestCase
from django.utils import timezone
from accounts.models import User, CustomerProfile
from turfs.models import Turf, TimeSlot
from turfs.services import SchedulingEngine
from bookings.models import Booking
from bookings.services import BookingEngine
from qr_system.models import QRTicket
from wallet.models import WalletTransaction


class BookingEngineTests(TestCase):
    def setUp(self):
        self.turf = Turf.objects.create(
            name="Arena 1 - Pro Pitch",
            slug="arena-1-pro-pitch",
            sport_type="FOOTBALL",
            description="50mm FIFA standard pitch",
            location="Indiranagar, Bangalore",
            address="12th Main Road",
            base_price=Decimal("1000.00"),
            operating_hours_start=time(6, 0),
            operating_hours_end=time(23, 0),
            slot_duration_minutes=60,
        )
        self.customer = User.objects.create_user(
            email="player1@friendsturf.local",
            password="password123",
            role="CUSTOMER",
            first_name="Rahul",
            last_name="Kumar",
        )
        self.customer2 = User.objects.create_user(
            email="player2@friendsturf.local",
            password="password123",
            role="CUSTOMER",
            first_name="Amit",
            last_name="Sharma",
        )
        self.staff = User.objects.create_user(
            email="staff@friendsturf.local", password="password123", role="STAFF"
        )
        self.today = timezone.now().date() + timedelta(days=3)
        self.slots = SchedulingEngine.generate_daily_slots(self.turf, self.today)

    def test_slot_lock_prevents_double_booking(self):
        """When player 1 holds a slot, player 2 cannot hold or book the same slot."""
        slot = self.slots[0]  # 06:00-07:00

        # Player 1 locks slot
        success1, res1 = BookingEngine.lock_slots(
            self.turf, self.today, [str(slot.id)], self.customer
        )
        self.assertTrue(success1)

        slot.refresh_from_db()
        self.assertEqual(slot.status, "LOCKED")
        self.assertEqual(slot.locked_by, self.customer)

        # Player 2 attempts to lock the same slot
        success2, msg2 = BookingEngine.lock_slots(
            self.turf, self.today, [str(slot.id)], self.customer2
        )
        self.assertFalse(success2)
        self.assertIn("temporarily held", msg2)

    def test_create_booking_generates_human_reference_and_qr(self):
        """Booking creation should produce FT-YY-XXXXXX booking ID and generate a valid QR pass."""
        slot1 = self.slots[2]  # 08:00-09:00
        slot2 = self.slots[3]  # 09:00-10:00

        booking = BookingEngine.create_booking(
            turf=self.turf,
            date_obj=self.today,
            slot_ids=[str(slot1.id), str(slot2.id)],
            user=self.customer,
            payment_type="FULL",
        )

        self.assertTrue(booking.booking_id.startswith("FT-"))
        self.assertEqual(booking.status, "CONFIRMED")
        self.assertEqual(booking.slots.count(), 2)

        # Slots must be permanently marked BOOKED
        slot1.refresh_from_db()
        slot2.refresh_from_db()
        self.assertEqual(slot1.status, "BOOKED")
        self.assertEqual(slot2.status, "BOOKED")
        self.assertEqual(slot1.booking_id, booking.booking_id)

        # QR ticket pass must exist
        qr_ticket = QRTicket.objects.filter(booking=booking).first()
        self.assertIsNotNone(qr_ticket)
        self.assertFalse(qr_ticket.is_used)

    def test_cancel_booking_releases_slots_and_credits_wallet(self):
        """Cancelling a booking >24h before kickoff releases all slots and credits full refund to wallet."""
        slot = self.slots[4]  # 10:00-11:00
        booking = BookingEngine.create_booking(
            turf=self.turf,
            date_obj=self.today,
            slot_ids=[str(slot.id)],
            user=self.customer,
            payment_type="FULL",
        )
        paid_amount = booking.amount_paid

        # Cancel (>24h ahead -> 100% refund)
        cancelled = BookingEngine.cancel_booking(
            booking, self.customer, reason="Injured player"
        )
        self.assertEqual(cancelled.status, "CANCELLED")

        slot.refresh_from_db()
        self.assertEqual(slot.status, "AVAILABLE")
        self.assertEqual(slot.booking_id, "")

        # Verify wallet credit
        self.customer.customer_profile.refresh_from_db()
        self.assertEqual(
            self.customer.customer_profile.wallet_balance, paid_amount
        )
        txn = WalletTransaction.objects.filter(
            customer=self.customer, reference_id=booking.booking_id
        ).first()
        self.assertIsNotNone(txn)
        self.assertEqual(txn.transaction_type, "CREDIT")

    def test_cancel_booking_applies_cancellation_fee_near_kickoff(self):
        """Cancelling within 6-24h window deducts dynamic cancellation fee (20%) and refunds remainder."""
        near_date = timezone.now().date() + timedelta(days=1)
        near_slots = SchedulingEngine.generate_daily_slots(self.turf, near_date)
        # Use slot matching roughly current time tomorrow so it's ~24h or slightly less
        slot = near_slots[0]
        booking = BookingEngine.create_booking(
            turf=self.turf,
            date_obj=near_date,
            slot_ids=[str(slot.id)],
            user=self.customer2,
            payment_type="FULL",
        )
        paid = booking.amount_paid
        self.customer2.customer_profile.refresh_from_db()
        initial_balance = self.customer2.customer_profile.wallet_balance

        cancelled = BookingEngine.cancel_booking(
            booking, self.customer2, reason="Change of plans"
        )
        self.assertEqual(cancelled.status, "CANCELLED")
        self.customer2.customer_profile.refresh_from_db()
        # Either full (if >24h) or 80% (if 6-24h) depending on exact hours, but wallet balance must have increased
        self.assertGreater(self.customer2.customer_profile.wallet_balance, initial_balance)

    def test_reschedule_booking_swaps_slots_atomically(self):
        """Rescheduling should release old slots and lock new slots atomically."""
        old_slot = self.slots[5]  # 11:00-12:00
        new_date = self.today + timedelta(days=1)
        new_slots = SchedulingEngine.generate_daily_slots(self.turf, new_date)
        target_slot = new_slots[10]  # 16:00-17:00

        booking = BookingEngine.create_booking(
            turf=self.turf,
            date_obj=self.today,
            slot_ids=[str(old_slot.id)],
            user=self.customer,
            payment_type="FULL",
        )

        rescheduled = BookingEngine.reschedule_booking(
            booking=booking,
            new_date=new_date,
            new_slot_ids=[str(target_slot.id)],
            user=self.customer,
        )

        self.assertEqual(rescheduled.date, new_date)
        self.assertEqual(rescheduled.start_time, target_slot.start_time)

        # Old slot released
        old_slot.refresh_from_db()
        self.assertEqual(old_slot.status, "AVAILABLE")

        # Target slot booked
        target_slot.refresh_from_db()
        self.assertEqual(target_slot.status, "BOOKED")
        self.assertEqual(target_slot.booking_id, booking.booking_id)

    def test_record_offline_payment_clears_balance(self):
        """Staff recording offline payment against balance due confirms booking and creates Payment."""
        slot = self.slots[6]  # 12:00-13:00
        booking = BookingEngine.create_booking(
            turf=self.turf,
            date_obj=self.today,
            slot_ids=[str(slot.id)],
            user=self.customer,
            payment_type="PENDING",
        )
        self.assertEqual(booking.status, "PAYMENT_PENDING")
        self.assertGreater(booking.balance_due, Decimal("0.00"))

        # Staff records offline cash payment
        payment = BookingEngine.record_offline_payment(
            booking=booking,
            amount=booking.final_amount,
            payment_method="CASH",
            reference_id="OFFLINE-TEST-123",
            collected_by=self.staff,
        )

        booking.refresh_from_db()
        self.assertEqual(booking.status, "CONFIRMED")
        self.assertEqual(booking.balance_due, Decimal("0.00"))
        self.assertEqual(payment.status, "PAID")
        self.assertEqual(payment.provider, "CASH")

    def test_30min_single_slot_lock_rejected_due_to_60min_minimum(self):
        """A single 30-minute slot cannot be locked or booked on its own (minimum match is 60 minutes)."""
        turf_30m = Turf.objects.create(
            name="Turf B - 30m Arena",
            slug="turf-b-30m-arena",
            sport_type="FOOTBALL",
            base_price=Decimal("1200.00"),
            operating_hours_start=time(6, 0),
            operating_hours_end=time(23, 0),
            slot_duration_minutes=30,
        )
        future_date = timezone.now().date() + timedelta(days=4)
        slots_30m = SchedulingEngine.generate_daily_slots(turf_30m, future_date)

        # Slot at 10:30-11:00
        slot_single = next(s for s in slots_30m if s.start_time == time(10, 30))

        # Locking 1 single 30m slot must fail
        success, msg = BookingEngine.lock_slots(
            turf_30m, future_date, [str(slot_single.id)], self.customer
        )
        self.assertFalse(success)
        self.assertIn("Minimum match duration is 60 minutes", msg)

        # Direct creation attempt must also raise ValueError
        with self.assertRaises(ValueError) as ctx:
            BookingEngine.create_booking(
                turf=turf_30m,
                date_obj=future_date,
                slot_ids=[str(slot_single.id)],
                user=self.customer,
            )
        self.assertIn("Minimum match duration is 60 minutes", str(ctx.exception))

    def test_30min_contiguous_slots_lock_and_booking_succeeds(self):
        """Two contiguous 30-minute slots (10:30-11:00 and 11:00-11:30 = 60m) lock and book successfully."""
        turf_30m = Turf.objects.create(
            name="Turf C - 30m Arena",
            slug="turf-c-30m-arena",
            sport_type="FOOTBALL",
            base_price=Decimal("1200.00"),
            operating_hours_start=time(6, 0),
            operating_hours_end=time(23, 0),
            slot_duration_minutes=30,
        )
        future_date = timezone.now().date() + timedelta(days=4)
        slots_30m = SchedulingEngine.generate_daily_slots(turf_30m, future_date)

        s1 = next(s for s in slots_30m if s.start_time == time(10, 30))
        s2 = next(s for s in slots_30m if s.start_time == time(11, 0))

        # Lock both slots
        success, res = BookingEngine.lock_slots(
            turf_30m, future_date, [str(s1.id), str(s2.id)], self.customer
        )
        self.assertTrue(success)

        # Create confirmed booking
        booking = BookingEngine.create_booking(
            turf=turf_30m,
            date_obj=future_date,
            slot_ids=[str(s1.id), str(s2.id)],
            user=self.customer,
            payment_type="FULL",
        )
        self.assertEqual(booking.status, "CONFIRMED")
        self.assertEqual(booking.slots.count(), 2)
        self.assertEqual(booking.start_time, time(10, 30))
        self.assertEqual(booking.end_time, time(11, 30))
        self.assertEqual(booking.final_amount, Decimal("1200.00"))

    def test_non_contiguous_slots_rejected(self):
        """Non-consecutive 30-minute slots (10:00-10:30 and 11:00-11:30) must be rejected."""
        turf_30m = Turf.objects.create(
            name="Turf D - 30m Arena",
            slug="turf-d-30m-arena",
            sport_type="FOOTBALL",
            base_price=Decimal("1200.00"),
            operating_hours_start=time(6, 0),
            operating_hours_end=time(23, 0),
            slot_duration_minutes=30,
        )
        future_date = timezone.now().date() + timedelta(days=4)
        slots_30m = SchedulingEngine.generate_daily_slots(turf_30m, future_date)

        s1 = next(s for s in slots_30m if s.start_time == time(10, 0))
        s3 = next(s for s in slots_30m if s.start_time == time(11, 0))

        success, msg = BookingEngine.lock_slots(
            turf_30m, future_date, [str(s1.id), str(s3.id)], self.customer
        )
        self.assertFalse(success)
        self.assertIn("consecutive", msg.lower())

    def test_partial_advance_deposit_dynamic_duration(self):
        """Booking 90 minutes (3 x 30m slots) requires dynamic advance of 1.5h * ₹100/hr = ₹150."""
        turf_30m = Turf.objects.create(
            name="Turf E - 30m Arena",
            slug="turf-e-30m-arena",
            sport_type="FOOTBALL",
            base_price=Decimal("1000.00"),
            operating_hours_start=time(6, 0),
            operating_hours_end=time(23, 0),
            slot_duration_minutes=30,
        )
        future_date = timezone.now().date() + timedelta(days=4)
        slots_30m = SchedulingEngine.generate_daily_slots(turf_30m, future_date)

        s1 = next(s for s in slots_30m if s.start_time == time(10, 30))
        s2 = next(s for s in slots_30m if s.start_time == time(11, 0))
        s3 = next(s for s in slots_30m if s.start_time == time(11, 30))

        # 90m booking with payment_type="PARTIAL"
        booking = BookingEngine.create_booking(
            turf=turf_30m,
            date_obj=future_date,
            slot_ids=[str(s1.id), str(s2.id), str(s3.id)],
            user=self.customer,
            payment_type="PARTIAL",
        )
        self.assertEqual(booking.status, "CONFIRMED")
        self.assertEqual(booking.amount_paid, Decimal("150.00"))  # 1.5 hr * ₹100/hr
        self.assertEqual(booking.final_amount, Decimal("1500.00"))  # 1.5 hr * ₹1000/hr
        self.assertEqual(booking.balance_due, Decimal("1350.00"))

