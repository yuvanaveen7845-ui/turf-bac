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
        self.today = timezone.now().date()
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
        """Cancelling a booking releases all slots back to AVAILABLE and credits refund to wallet."""
        slot = self.slots[4]  # 10:00-11:00
        booking = BookingEngine.create_booking(
            turf=self.turf,
            date_obj=self.today,
            slot_ids=[str(slot.id)],
            user=self.customer,
            payment_type="FULL",
        )
        paid_amount = booking.amount_paid

        # Cancel
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
