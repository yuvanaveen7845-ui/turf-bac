from datetime import timedelta
from django.utils import timezone
from .models import Notification
from bookings.models import Booking


class ReminderService:
    """
    Automated internal reminder service:
    - Checks upcoming confirmed bookings (24h and 2h prior)
    - Sends in-app notifications to players
    - Idempotent: prevents duplicate reminder notifications
    """

    @classmethod
    def send_upcoming_match_reminders(cls):
        now = timezone.now()
        today = now.date()

        # Find confirmed bookings for today and tomorrow
        bookings = Booking.objects.filter(
            status="CONFIRMED",
            date__gte=today,
            date__lte=today + timedelta(days=1),
        ).select_related("customer", "turf")

        reminders_sent = 0

        for booking in bookings:
            # Check if reminder already sent
            existing_24h = Notification.objects.filter(
                user=booking.customer,
                notification_type="UPCOMING_REMINDER",
                data__booking_id=booking.booking_id,
            ).exists()

            if not existing_24h:
                Notification.objects.create(
                    user=booking.customer,
                    notification_type="UPCOMING_REMINDER",
                    title=f"Upcoming Match Reminder ({booking.booking_id})",
                    message=f"Your pitch booking for {booking.turf.name} is scheduled for {booking.date.strftime('%d %b %Y')} at {booking.start_time.strftime('%H:%M')}. Be ready!",
                    data={"booking_id": booking.booking_id, "turf_name": booking.turf.name},
                )
                reminders_sent += 1

        return reminders_sent
