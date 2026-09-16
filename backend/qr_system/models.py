import uuid
import hashlib
import secrets
from django.db import models
from django.conf import settings
from django.utils import timezone
from bookings.models import Booking


class QRCredential(models.Model):
    STATUS_CHOICES = (
        ("ACTIVE", "Active"),
        ("USED", "Used"),
        ("EXPIRED", "Expired"),
        ("REVOKED", "Revoked"),
        ("CANCELLED", "Cancelled"),
    )

    booking = models.OneToOneField(
        Booking, on_delete=models.CASCADE, related_name="qr_credential"
    )
    credential_token = models.CharField(max_length=128, unique=True, db_index=True)
    credential_hash = models.CharField(max_length=64, unique=True, db_index=True)
    credential_version = models.IntegerField(default=1)
    status = models.CharField(
        max_length=20, choices=STATUS_CHOICES, default="ACTIVE", db_index=True
    )
    issued_at = models.DateTimeField(default=timezone.now)
    valid_from = models.DateTimeField(db_index=True)
    valid_until = models.DateTimeField(db_index=True)

    # Scan telemetry
    last_scanned_at = models.DateTimeField(null=True, blank=True)
    scan_count = models.PositiveIntegerField(default=0)

    # Check-in link
    checkin_at = models.DateTimeField(null=True, blank=True)
    checkin_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="admitted_credentials",
    )

    # Revocation tracking
    revoked_at = models.DateTimeField(null=True, blank=True)
    revocation_reason = models.TextField(blank=True)

    # Rendered QR code asset
    qr_base64 = models.TextField(blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "QR Credential"
        verbose_name_plural = "QR Credentials"

    def __str__(self):
        return f"QRCredential ({self.status}) for {self.booking.booking_id} [v{self.credential_version}]"

    @classmethod
    def generate_token(cls):
        # Cryptographically secure random URL-safe token
        return f"FT-PASS-{secrets.token_urlsafe(24)}"

    @classmethod
    def compute_hash(cls, token: str) -> str:
        return hashlib.sha256(token.encode("utf-8")).hexdigest()

    @property
    def is_used(self):
        return self.status == "USED"

    @property
    def ticket_code(self):
        return self.credential_token


class CheckIn(models.Model):
    METHOD_CHOICES = (
        ("QR_SCAN", "QR Camera Optical Scan"),
        ("MANUAL_CODE", "Manual Booking Code Lookup"),
        ("ADMIN_OVERRIDE", "Admin Override"),
        ("WALK_IN", "Walk-in Desk Reception"),
    )

    DECISION_CHOICES = (
        ("ALLOW", "Entry Approved"),
        ("DENY", "Entry Denied"),
        ("WARNING", "Warning / Partial Payment Required"),
        ("MANUAL_REVIEW", "Manual Review Required"),
    )

    booking = models.ForeignKey(
        Booking, on_delete=models.CASCADE, related_name="check_ins"
    )
    qr_credential = models.ForeignKey(
        QRCredential,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="check_ins",
    )
    staff_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="check_in_operations",
    )
    turf = models.ForeignKey(
        "turfs.Turf",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="turf_check_ins",
    )
    check_in_time = models.DateTimeField(default=timezone.now, db_index=True)
    method = models.CharField(
        max_length=30, choices=METHOD_CHOICES, default="QR_SCAN"
    )
    decision = models.CharField(
        max_length=30, choices=DECISION_CHOICES, default="ALLOW"
    )
    reason_code = models.CharField(max_length=50, default="ENTRY_APPROVED")
    message = models.CharField(max_length=255)
    override_reason = models.TextField(blank=True)
    device_identifier = models.CharField(max_length=100, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-check_in_time"]
        verbose_name = "Gate Check-In"
        verbose_name_plural = "Gate Check-Ins"

    def __str__(self):
        return f"CheckIn: {self.booking.booking_id} by {self.staff_user.email} -> {self.decision} ({self.reason_code})"


# Backward compatibility aliases
QRTicket = QRCredential
CheckInRecord = CheckIn
