from datetime import timedelta
import logging
from django.utils import timezone
from django.core.mail import EmailMultiAlternatives
from django.conf import settings
from .models import Notification
from bookings.models import Booking
from .email_templates import (
    render_booking_confirmation_email,
    render_payment_receipt_email,
    render_otp_verification_email,
)

logger = logging.getLogger(__name__)


class EmailNotificationService:
    """
    Enterprise Email Notification Service for Friends Turf.
    Sends responsive, branded HTML emails with embedded logos and QR credentials.
    """

    @classmethod
    def send_otp_email(
        cls, user, otp_code: str, valid_minutes: int = 10, ip_address: str = ""
    ) -> bool:
        """
        Sends branded one-time password (OTP) verification email via Django SMTP.
        """
        recipient_email = user.email.strip()
        if not recipient_email:
            return False

        try:
            subject = f"Your Password Reset OTP: {otp_code} — Friends Turf"
            html_content = render_otp_verification_email(
                user=user,
                otp_code=otp_code,
                valid_minutes=valid_minutes,
                ip_address=ip_address,
            )
            plain_text = (
                f"Hello {user.full_name or 'Player'},\n\n"
                f"Your one-time passcode (OTP) for resetting your Friends Turf account password is: {otp_code}\n\n"
                f"This code is valid for {valid_minutes} minutes. Never share this code with anyone."
            )

            from_email = getattr(
                settings, "DEFAULT_FROM_EMAIL", "Friends Turf <support@friendsturf.com>"
            )

            msg = EmailMultiAlternatives(
                subject=subject,
                body=plain_text,
                from_email=from_email,
                to=[recipient_email],
            )
            msg.attach_alternative(html_content, "text/html")
            msg.send(fail_silently=False)
            logger.info(f"Successfully sent password reset OTP email to {recipient_email}")
            return True
        except Exception as e:
            logger.error(f"Failed to send password reset OTP email to {recipient_email}: {e}")
            return False


    @classmethod
    def send_booking_confirmation_email(
        cls, booking, qr_base64: str = "", recipient_email: str = ""
    ) -> bool:
        """
        Sends official match pass confirmation email with turnstile QR pass.
        If recipient_email is not provided, defaults to booking.customer.email.
        """
        target_email = (recipient_email or getattr(booking.customer, "email", "") or "").strip()
        if not target_email:
            logger.warning(f"Cannot send match pass email for booking {booking.booking_id}: no email address found.")
            return False

        # If QR code was not provided, fetch or generate it dynamically
        if not qr_base64:
            try:
                from qr_system.services import QRService
                credential = QRService.generate_credential_for_booking(booking)
                if credential and credential.qr_base64:
                    qr_base64 = credential.qr_base64
            except Exception as e:
                logger.warning(f"Could not load QR code for email match pass: {e}")

        try:
            match_date_str = booking.date.strftime("%d %b %Y")
            subject = f"Match Pass Confirmed: {booking.turf.name} (#{booking.booking_id}) — Friends Turf"
            html_content = render_booking_confirmation_email(booking, qr_base64)
            plain_text = (
                f"Your match pass for {booking.turf.name} on {match_date_str} is confirmed.\n"
                f"Booking ID: {booking.booking_id}\n"
                f"Timing: {booking.start_time.strftime('%I:%M %p')} - {booking.end_time.strftime('%I:%M %p')}\n"
                f"Location: {booking.turf.location}\n"
                f"Present your match pass code #{booking.booking_id} at the gate turnstile scanner."
            )

            from_email = getattr(
                settings, "DEFAULT_FROM_EMAIL", "Friends Turf <support@friendsturf.com>"
            )

            msg = EmailMultiAlternatives(
                subject=subject,
                body=plain_text,
                from_email=from_email,
                to=[target_email],
            )
            msg.attach_alternative(html_content, "text/html")
            msg.send(fail_silently=False)
            logger.info(f"Successfully sent match pass email for {booking.booking_id} to {target_email}")
            return True
        except Exception as e:
            logger.error(f"Failed to send booking confirmation email to {target_email}: {e}")
            return False


    @classmethod
    def send_receipt_invoice_email(cls, receipt_data: dict):
        """Sends official GST Tax Invoice email."""
        recipient_email = receipt_data.get("customer", {}).get("email")
        if not recipient_email:
            return False

        try:
            receipt_no = receipt_data.get("receipt_number", "REC-26-0000")
            subject = f"Official GST Tax Invoice ({receipt_no}) — Friends Turf"
            html_content = render_payment_receipt_email(receipt_data)
            plain_text = f"Official payment receipt {receipt_no} for Friends Turf booking. View in your portal."

            msg = EmailMultiAlternatives(
                subject=subject,
                body=plain_text,
                from_email=getattr(settings, "DEFAULT_FROM_EMAIL", "Friends Turf <support@friendsturf.com>"),
                to=[recipient_email],
            )
            msg.attach_alternative(html_content, "text/html")
            msg.send(fail_silently=True)
            return True
        except Exception as e:
            logger.error(f"Failed to send receipt email to {recipient_email}: {e}")
            return False


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
