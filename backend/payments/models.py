import uuid
from decimal import Decimal
from django.db import models
from django.conf import settings
from django.utils import timezone
from bookings.models import Booking


class Payment(models.Model):
    STATUS_CHOICES = (
        ("PENDING", "Pending"),
        ("PROCESSING", "Processing"),
        ("PAID", "Paid"),
        ("PARTIALLY_PAID", "Partially Paid"),
        ("SUCCESSFUL", "Successful"),
        ("FAILED", "Failed"),
        ("TIMEOUT", "Timeout"),
        ("REFUNDED", "Refunded"),
        ("PARTIALLY_REFUNDED", "Partially Refunded"),
    )

    PROVIDER_CHOICES = (
        ("RAZORPAY", "Razorpay"),
        ("WALLET", "Turf Wallet"),
        ("CASH", "Cash on Venue"),
    )

    METHOD_CHOICES = (
        ("UPI", "UPI / QR"),
        ("CARD", "Credit / Debit Card"),
        ("WALLET", "Turf Wallet"),
        ("NET_BANKING", "Net Banking"),
        ("CASH", "Cash on Venue (Walk-in)"),
        ("BANK_TRANSFER", "Bank Transfer"),
        ("OTHER", "Other"),
    )

    TYPE_CHOICES = (
        ("FULL", "Full Payment"),
        ("PARTIAL", "Partial / Advance Payment"),
        ("BALANCE", "Balance Payment"),
    )

    payment_id = models.CharField(max_length=30, unique=True, db_index=True)
    booking = models.ForeignKey(
        Booking, on_delete=models.CASCADE, related_name="payments", null=True, blank=True
    )
    customer = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="payments"
    )
    provider = models.CharField(
        max_length=20, choices=PROVIDER_CHOICES, default="RAZORPAY"
    )
    provider_order_id = models.CharField(
        max_length=100, blank=True, db_index=True
    )
    provider_payment_id = models.CharField(
        max_length=100, blank=True, db_index=True
    )
    provider_signature = models.CharField(max_length=255, blank=True)
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    currency = models.CharField(max_length=10, default="INR")
    payment_method = models.CharField(
        max_length=20, choices=METHOD_CHOICES, default="UPI"
    )
    payment_type = models.CharField(max_length=20, choices=TYPE_CHOICES, default="FULL")
    transaction_reference = models.CharField(max_length=100, unique=True, db_index=True)
    status = models.CharField(
        max_length=20, choices=STATUS_CHOICES, default="PENDING", db_index=True
    )
    failure_reason = models.TextField(blank=True)
    idempotency_key = models.CharField(
        max_length=100, blank=True, null=True, unique=True
    )
    gateway_response = models.JSONField(default=dict, blank=True)
    collected_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="collected_payments",
    )
    notes = models.TextField(blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    paid_at = models.DateTimeField(null=True, blank=True)
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
        ("REQUESTED", "Requested"),
        ("PENDING", "Pending"),
        ("PROCESSING", "Processing"),
        ("COMPLETED", "Completed"),
        ("FAILED", "Failed"),
        ("PARTIALLY_REFUNDED", "Partially Refunded"),
    )

    REFUND_TO_CHOICES = (
        ("WALLET", "Credit to Turf Wallet (Instant)"),
        ("ORIGINAL", "Original Payment Method (3-5 Days)"),
        ("CASH", "Cash at Counter / Desk"),
    )

    refund_id = models.CharField(max_length=30, unique=True, db_index=True)
    payment = models.ForeignKey(
        Payment, on_delete=models.CASCADE, related_name="refunds"
    )
    booking = models.ForeignKey(
        Booking, on_delete=models.CASCADE, related_name="refunds"
    )
    provider_refund_id = models.CharField(
        max_length=100, blank=True, db_index=True
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
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="COMPLETED")
    reason = models.TextField()
    reference_id = models.CharField(max_length=100, blank=True)
    initiated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="initiated_refunds",
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.refund_id} | Booking: {self.booking.booking_id} | ₹{self.amount} [{self.status}]"


class DailyCashDrawer(models.Model):
    """
    Daily Cash Drawer reconciliation model:
    Tracks opening cash, daily cash collections, desk cash refunds, expected closing,
    and verified actual closing cash count by staff.
    """
    STATUS_CHOICES = (
        ("OPEN", "Open"),
        ("CLOSED", "Closed"),
        ("DISCREPANCY", "Discrepancy Flagged"),
    )

    date = models.DateField(unique=True, default=timezone.now)
    opening_cash = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal("0.00"))
    actual_closing_cash = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    closed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="closed_cash_drawers",
    )
    closed_at = models.DateTimeField(null=True, blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="OPEN")
    notes = models.TextField(blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-date"]

    def __str__(self):
        return f"Cash Drawer {self.date} [{self.status}] - Open ₹{self.opening_cash}"

    def get_summary(self):
        """
        Calculates live metrics for the day's cash operations.
        """
        cash_payments = Payment.objects.filter(
            created_at__date=self.date,
            status__in=["PAID", "SUCCESSFUL"],
            payment_method="CASH",
        )
        total_collected = cash_payments.aggregate(models.Sum("amount"))["amount__sum"] or Decimal("0.00")
        payment_count = cash_payments.count()

        cash_refunds = Refund.objects.filter(
            created_at__date=self.date,
            status="COMPLETED",
            refund_to="CASH",
        )
        total_refunded = cash_refunds.aggregate(models.Sum("amount"))["amount__sum"] or Decimal("0.00")
        refund_count = cash_refunds.count()

        expected_closing = self.opening_cash + total_collected - total_refunded

        variance = None
        has_discrepancy = False
        if self.actual_closing_cash is not None:
            variance = self.actual_closing_cash - expected_closing
            has_discrepancy = abs(variance) > Decimal("0.01")

        return {
            "date": str(self.date),
            "status": self.status,
            "opening_cash": float(self.opening_cash),
            "offline_cash_collected": float(total_collected),
            "cash_payments_count": payment_count,
            "cash_refunds_processed": float(total_refunded),
            "cash_refunds_count": refund_count,
            "expected_closing": float(expected_closing),
            "actual_closing_cash": float(self.actual_closing_cash) if self.actual_closing_cash is not None else None,
            "variance": float(variance) if variance is not None else None,
            "has_discrepancy": has_discrepancy,
            "closed_at": self.closed_at.isoformat() if self.closed_at else None,
            "closed_by": self.closed_by.email if self.closed_by else None,
            "notes": self.notes,
        }
