import uuid
from django.db import models
from django.conf import settings
from bookings.models import Booking


class QRTicket(models.Model):
    booking = models.OneToOneField(
        Booking, on_delete=models.CASCADE, related_name="qr_ticket"
    )
    ticket_code = models.CharField(max_length=64, unique=True, db_index=True)
    jwt_token = models.TextField()
    qr_base64 = models.TextField(blank=True)
    is_used = models.BooleanField(default=False)
    used_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"QR Ticket for {self.booking.booking_id} - {'USED' if self.is_used else 'ACTIVE'}"

    @classmethod
    def generate_ticket_code(cls):
        return f"TKT-{uuid.uuid4().hex[:12].upper()}"


class CheckInRecord(models.Model):
    RESULT_CHOICES = (
        ("VALID", "Booking Verified — Entry Allowed"),
        ("INVALID", "Invalid Booking"),
        ("ALREADY_USED", "Customer Already Checked In"),
        ("EXPIRED", "Booking Expired"),
    )

    booking = models.ForeignKey(
        Booking, on_delete=models.CASCADE, related_name="checkin_records"
    )
    scanned_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="checkin_logs"
    )
    scanned_at = models.DateTimeField(auto_now_add=True)
    result = models.CharField(max_length=30, choices=RESULT_CHOICES)
    message = models.CharField(max_length=255)
    notes = models.TextField(blank=True)

    def __str__(self):
        return f"CheckIn: {self.booking.booking_id} by {self.scanned_by.email} -> {self.result}"
