import os
import re
import requests
import base64
from email.mime.image import MIMEImage
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
    def send_booking_confirmation_email_async(
        cls, booking_or_id, qr_base64: str = "", recipient_email: str = ""
    ):
        """
        Dispatches booking confirmation email in a non-blocking background daemon thread.
        Guarantees that payment webhooks and user API requests respond immediately (0ms delay)
        without blocking on external SMTP socket connections or causing Gunicorn worker timeouts.
        Under test runners, executes synchronously so mocks work cleanly without SQLite multi-thread locks.
        """
        import sys
        if "test" in sys.argv:
            if hasattr(booking_or_id, "booking_id"):
                return cls.send_booking_confirmation_email(booking_or_id, qr_base64, recipient_email)
            from bookings.models import Booking
            b = Booking.objects.filter(booking_id=str(booking_or_id)).first()
            if b:
                return cls.send_booking_confirmation_email(b, qr_base64, recipient_email)
            return False

        import threading
        booking_id = getattr(booking_or_id, "booking_id", None) or getattr(booking_or_id, "id", None) or str(booking_or_id)

        def _worker():
            from django.db import connection
            try:
                from bookings.models import Booking
                if hasattr(booking_or_id, "booking_id"):
                    b = booking_or_id
                else:
                    b = Booking.objects.filter(booking_id=booking_id).first()
                    if not b and str(booking_id).isdigit():
                        b = Booking.objects.filter(pk=int(booking_id)).first()
                if b:
                    cls.send_booking_confirmation_email(
                        booking=b,
                        qr_base64=qr_base64,
                        recipient_email=recipient_email,
                    )
            except Exception as ex:
                logger.error(f"Background email dispatcher failed for booking {booking_id}: {ex}")
            finally:
                connection.close()

        thread = threading.Thread(target=_worker, name=f"EmailDispatch-{booking_id}", daemon=True)
        thread.start()

    @classmethod
    def send_booking_confirmation_email(
        cls, booking, qr_base64: str = "", recipient_email: str = ""
    ) -> bool:
        """
        Sends official match pass confirmation email with turnstile QR pass.
        If recipient_email is not provided, defaults to booking.customer.email.
        Embeds QR pass as an inline MIME image (cid:match_pass_qr) for flawless display in Gmail/Outlook.
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
            has_qr = bool(qr_base64)
            qr_image_src = "cid:match_pass_qr" if has_qr else ""
            html_content = render_booking_confirmation_email(booking, qr_image_src=qr_image_src)
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

            # Attach the QR image as an inline MIME part within multipart/related
            if has_qr:
                try:
                    raw_b64 = qr_base64.split(",")[-1]
                    img_bytes = base64.b64decode(raw_b64)
                    mime_img = MIMEImage(img_bytes, _subtype="png")
                    mime_img.add_header("Content-ID", "<match_pass_qr>")
                    mime_img.add_header("Content-Disposition", "inline", filename=f"match_pass_{booking.booking_id}.png")
                    msg.attach(mime_img)
                except Exception as img_err:
                    logger.warning(f"Could not attach inline QR image to email: {img_err}")

            sent_count = msg.send(fail_silently=True)
            if sent_count:
                logger.info(f"Successfully sent match pass email for {booking.booking_id} to {target_email}")
                return True
            else:
                logger.warning(f"SMTP mail backend returned 0 sent messages for {booking.booking_id} to {target_email}")
                return False
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


class SMSNotificationService:
    """
    Enterprise SMS Notification Service for Friends Turf.
    Supports multi-provider dispatch:
    1. Fast2SMS (Indian OTP Gateway)
    2. Twilio (Global SMS & WhatsApp)
    3. Development / Mock fallback (Logs to console & system logger)
    """

    @classmethod
    def send_login_otp(cls, phone: str, otp_code: str, valid_minutes: int = 10) -> bool:
        """
        Dispatches OTP code to the recipient mobile number.
        """
        clean_phone = re.sub(r"[^\d+]", "", str(phone or "").strip())
        raw_10 = clean_phone[-10:] if len(clean_phone) >= 10 else clean_phone

        sms_message = (
            f"Your Friends Turf login OTP is: {otp_code}. "
            f"Valid for {valid_minutes} minutes. Please do not share this passcode with anyone."
        )

        # 1. Fast2SMS Integration (Indian Gateway)
        fast2sms_key = os.environ.get("FAST2SMS_API_KEY", "").strip()
        if fast2sms_key and len(raw_10) == 10:
            try:
                url = "https://www.fast2sms.com/dev/bulkV2"
                payload = {
                    "variables_values": otp_code,
                    "route": "otp",
                    "numbers": raw_10,
                }
                headers = {
                    "authorization": fast2sms_key,
                    "Content-Type": "application/x-www-form-urlencoded",
                }
                resp = requests.post(url, data=payload, headers=headers, timeout=5)
                logger.info(f"Fast2SMS OTP sent to {raw_10}: {resp.status_code}")
                return resp.status_code == 200
            except Exception as e:
                logger.warning(f"Fast2SMS dispatch failed: {e}")

        # 2. Twilio SMS Integration
        twilio_sid = os.environ.get("TWILIO_ACCOUNT_SID", "").strip()
        twilio_token = os.environ.get("TWILIO_AUTH_TOKEN", "").strip()
        twilio_from = os.environ.get("TWILIO_PHONE_NUMBER", "").strip()
        if twilio_sid and twilio_token and twilio_from:
            try:
                to_number = f"+91{raw_10}" if not clean_phone.startswith("+") else clean_phone
                twilio_url = f"https://api.twilio.com/2010-04-01/Accounts/{twilio_sid}/Messages.json"
                resp = requests.post(
                    twilio_url,
                    auth=(twilio_sid, twilio_token),
                    data={"From": twilio_from, "To": to_number, "Body": sms_message},
                    timeout=5,
                )
                logger.info(f"Twilio OTP sent to {to_number}: {resp.status_code}")
                return resp.status_code in (200, 201)
            except Exception as e:
                logger.warning(f"Twilio dispatch failed: {e}")

        # 3. Development / Mock fallback
        logger.info(f"[DEV SMS GATEWAY] Dispatch to {clean_phone}: '{sms_message}'")
        return True

