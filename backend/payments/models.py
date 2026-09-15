import uuid
from django.db import models
from django.conf import settings
from bookings.models import Booking


class Payment(models.Model):
    STATUS_CHOICES = (
        ("PENDING", "Pending"),
        ("PROCESSING", "Processing"),
        ("SUCCESSFUL", "Successful"),
        ("FAILED", "Failed"),
        ("TIMEOUT", "Timeout"),
        ("REFUNDED", "Refunded"),
    )

    METHOD_CHOICES = (
        ("UPI", "UPI / QR"),
        ("CARD", "Credit / Debit Card"),
        ("WALLET", "Turf Wallet"),
        ("NET_BANKING", "Net Banking"),
        ("CASH", "Cash on Venue (Walk-in)"),
    )

    TYPE_CHOICES = (
        ("FULL", "Full Payment"),
        ("PARTIAL", "Partial / Advance Payment"),
        ("BALANCE", "Balance Payment"),
    )

    payment_id = models.CharField(max_length=30, unique=True, db_index=True)
    booking = models.ForeignKey(
        Booking, on_delete=models.CASCADE, related_name="payments"
    )
    customer = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="payments"
    )
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    payment_method = models.CharField(
        max_length=20, choices=METHOD_CHOICES, default="UPI"
    )
    payment_type = models.CharField(max_length=20, choices=TYPE_CHOICES, default="FULL")
    transaction_reference = models.CharField(max_length=100, unique=True, db_index=True)
    status = models.CharField(
        max_length=20, choices=STATUS_CHOICES, default="PENDING", db_index=True
    )
    gateway_response = models.JSONField(default=dict, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.payment_id} | {self.booking.booking_id} | ₹{self.amount} [{self.status}]"

    @classmethod
    def generate_payment_id(cls):
        return f"PAY-{uuid.uuid4().hex[:8].upper()}"


class Refund(models.Model):
    STATUS_CHOICES = (
        ("PENDING", "Pending"),
        ("PROCESSING", "Processing"),
        ("COMPLETED", "Completed"),
        ("FAILED", "Failed"),
    )

    REFUND_TO_CHOICES = (
        ("WALLET", "Credit to Turf Wallet (Instant)"),
        ("ORIGINAL", "Original Payment Method (3-5 Days)"),
    )

    refund_id = models.CharField(max_length=30, unique=True, db_index=True)
    payment = models.ForeignKey(
        Payment, on_delete=models.CASCADE, related_name="refunds"
    )
    booking = models.ForeignKey(
        Booking, on_delete=models.CASCADE, related_name="refunds"
    )
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    refund_type = models.CharField(
        max_length=20,
        choices=(("FULL", "Full Refund"), ("PARTIAL", "Partial Refund")),
        default="FULL",
    )
    refund_to = models.CharField(
        max_length=20, choices=REFUND_TO_CHOICES, default="WALLET"
    )
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="PENDING")
    reason = models.TextField()
    reference_id = models.CharField(max_length=100, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return f"{self.refund_id} | Booking: {self.booking.booking_id} | ₹{self.amount} [{self.status}]"
