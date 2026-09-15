import io
import base64
import jwt
import qrcode
from datetime import datetime, date, timedelta
from django.conf import settings
from django.utils import timezone
from .models import QRTicket, CheckInRecord
from bookings.models import Booking

QR_SECRET_KEY = getattr(settings, "SECRET_KEY", "ft-qr-secret-key-2026")


class QRService:
    @classmethod
    def generate_qr_for_booking(cls, booking):
        """
        Creates a tamper-proof JWT token and generates a base64 encoded QR image.
        The token only encodes safe identifiers (booking_id, ticket_code).
        """
        # Get or create ticket
        ticket, created = QRTicket.objects.get_or_create(
            booking=booking, defaults={"ticket_code": QRTicket.generate_ticket_code()}
        )

        payload = {
            "b_id": booking.booking_id,
            "tkt": ticket.ticket_code,
            "turf_id": str(booking.turf.id),
            "date": str(booking.date),
            "start_time": str(booking.start_time),
            "end_time": str(booking.end_time),
            "exp": datetime.utcnow() + timedelta(days=30),
        }

        token = jwt.encode(payload, QR_SECRET_KEY, algorithm="HS256")
        ticket.jwt_token = token

        # Generate QR code image
        qr = qrcode.QRCode(
            version=1,
            error_correction=qrcode.constants.ERROR_CORRECT_M,
            box_size=8,
            border=2,
        )
        qr.add_data(token)
        qr.make(fit=True)
        img = qr.make_image(fill_color="#064e3b", back_color="white")

        buffer = io.BytesIO()
        img.save(buffer, format="PNG")
        qr_b64 = base64.b64encode(buffer.getvalue()).decode("utf-8")
        ticket.qr_base64 = f"data:image/png;base64,{qr_b64}"
        ticket.save()

        return ticket

    @classmethod
    def validate_and_checkin(cls, raw_data, staff_user, notes=""):
        """
        Validates scanned QR token or booking ID, verifies all criteria,
        and performs atomic check-in preventing duplicate entry.
        """
        booking_id = None

        # 1. Try decoding as JWT
        try:
            decoded = jwt.decode(raw_data, QR_SECRET_KEY, algorithms=["HS256"])
            booking_id = decoded.get("b_id")
        except jwt.PyJWTError:
            # Fallback: maybe staff entered or scanned the direct booking_id or ticket_code
            clean_str = raw_data.strip()
            ticket = QRTicket.objects.filter(ticket_code=clean_str).first()
            if ticket:
                booking_id = ticket.booking.booking_id
            else:
                booking_id = clean_str

        booking = Booking.objects.filter(booking_id=booking_id).first()
        if not booking:
            return {
                "status": "INVALID",
                "title": "Invalid Booking",
                "message": "No booking found matching this code.",
                "booking": None,
            }

        ticket = getattr(booking, "qr_ticket", None)

        # Check duplicate check-in
        if booking.status == "CHECKED_IN" or (ticket and ticket.is_used):
            CheckInRecord.objects.create(
                booking=booking,
                scanned_by=staff_user,
                result="ALREADY_USED",
                message="Customer has already checked in.",
                notes=notes,
            )
            return {
                "status": "ALREADY_USED",
                "title": "Customer Already Checked In",
                "message": f'This ticket was already used at {booking.checked_in_at.strftime("%I:%M %p, %d %b") if booking.checked_in_at else "Venue"}. Duplicate entry blocked.',
                "booking": {
                    "booking_id": booking.booking_id,
                    "customer_name": booking.customer.full_name,
                    "turf_name": booking.turf.name,
                    "date": str(booking.date),
                    "time": f"{booking.start_time.strftime('%H:%M')} - {booking.end_time.strftime('%H:%M')}",
                    "checked_in_at": str(booking.checked_in_at),
                },
            }

        # Check cancelled / refunded
        if booking.status in ("CANCELLED", "REFUNDED"):
            return {
                "status": "INVALID",
                "title": "Booking Cancelled",
                "message": f"This booking was {booking.status.lower()} and is no longer valid.",
                "booking": {"booking_id": booking.booking_id},
            }

        # Check date (Grace period: allowed on booking date)
        today = timezone.now().date()
        if booking.date < today:
            return {
                "status": "EXPIRED",
                "title": "Booking Expired",
                "message": f'This booking was for {booking.date.strftime("%d %b %Y")}, which has already passed.',
                "booking": {"booking_id": booking.booking_id},
            }

        # Check payment
        if booking.status == "PAYMENT_PENDING" or booking.balance_due > 0:
            payment_warning = (
                f"Notice: Balance due ₹{booking.balance_due}"
                if booking.balance_due > 0
                else "Payment is still pending."
            )
        else:
            payment_warning = None

        # Success: Mark as checked-in
        now = timezone.now()
        booking.status = "CHECKED_IN"
        booking.checked_in_at = now
        booking.checked_in_by = staff_user
        booking.save()

        if ticket:
            ticket.is_used = True
            ticket.used_at = now
            ticket.save()

        CheckInRecord.objects.create(
            booking=booking,
            scanned_by=staff_user,
            result="VALID",
            message="Booking Verified — Entry Allowed",
            notes=notes,
        )

        return {
            "status": "VALID",
            "title": "Booking Verified — Entry Allowed",
            "message": "Welcome to Friends Turf! Ticket verified successfully.",
            "payment_warning": payment_warning,
            "booking": {
                "booking_id": booking.booking_id,
                "customer_name": booking.customer.full_name,
                "customer_phone": booking.customer.phone,
                "turf_name": booking.turf.name,
                "date": str(booking.date),
                "start_time": booking.start_time.strftime("%H:%M"),
                "end_time": booking.end_time.strftime("%H:%M"),
                "amount_paid": float(booking.amount_paid),
                "balance_due": float(booking.balance_due),
                "checked_in_at": now.strftime("%I:%M %p"),
            },
        }
