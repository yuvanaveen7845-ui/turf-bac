from django.test import TestCase
from django.core import mail
from decimal import Decimal
from datetime import date, time

from turfs.models import Turf
from accounts.models import User
from bookings.models import Booking
from notifications.services import EmailNotificationService


class NotificationEmailRecipientTests(TestCase):
    def setUp(self):
        self.turf = Turf.objects.create(
            name="Friends FIFA Arena",
            slug="friends-fifa-arena",
            sport_type="FOOTBALL",
            base_price=Decimal("1200.00"),
            is_active=True,
        )
        self.customer = User.objects.create_user(
            email="matchplayer@example.com",
            phone="+919876543299",
            password="TestPassword123!",
            first_name="Match",
            last_name="Player",
        )
        self.booking = Booking.objects.create(
            booking_id="FT-TEST-REC-01",
            customer=self.customer,
            turf=self.turf,
            date=date.today(),
            start_time=time(18, 0),
            end_time=time(19, 0),
            total_amount=Decimal("1200.00"),
            final_amount=Decimal("1200.00"),
            amount_paid=Decimal("300.00"),
            balance_due=Decimal("900.00"),
            status="CONFIRMED",
        )

    def test_booking_confirmation_email_recipients(self):
        # Trigger booking confirmation email
        success = EmailNotificationService.send_booking_confirmation_email(self.booking)
        self.assertTrue(success)

        # Inspect django.core.mail.outbox
        self.assertGreaterEqual(len(mail.outbox), 1)
        sent_msg = mail.outbox[-1]

        # Verify all intended admin and customer recipients
        self.assertIn("friendsturftirupur@gmail.com", sent_msg.to)
        self.assertIn("wertyv17@gmail.com", sent_msg.to)
        self.assertIn("matchplayer@example.com", sent_msg.to)

        # Ensure no duplicates in the recipient list
        self.assertEqual(len(sent_msg.to), len(set(sent_msg.to)))
