import uuid
from django.db import models
from django.conf import settings
from django.utils import timezone
from turfs.models import Turf, TimeSlot


class Booking(models.Model):
    STATUS_CHOICES = (
        ("UPCOMING", "Upcoming"),
        ("PAYMENT_PENDING", "Payment Pending"),
        ("CONFIRMED", "Confirmed"),
        ("CHECKED_IN", "Checked-in"),
        ("IN_PROGRESS", "In Progress"),
        ("COMPLETED", "Completed"),
        ("CANCELLED", "Cancelled"),
        ("NO_SHOW", "No-show"),
        ("REFUNDED", "Refunded"),
    )

    BOOKING_TYPE_CHOICES = (
        ("REGULAR", "Regular Booking"),
        ("RECURRING", "Recurring Booking"),
        ("GROUP", "Group / Team Booking"),
        ("WALK_IN", "Walk-in Booking"),
    )

    booking_id = models.CharField(max_length=30, unique=True, db_index=True)
    customer = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="bookings"
    )
    turf = models.ForeignKey(Turf, on_delete=models.PROTECT, related_name="bookings")
    date = models.DateField(db_index=True)
    start_time = models.TimeField()
    end_time = models.TimeField()
    slots = models.ManyToManyField(TimeSlot, related_name="slot_bookings", blank=True)

    booking_type = models.CharField(
        max_length=20, choices=BOOKING_TYPE_CHOICES, default="REGULAR"
    )
    status = models.CharField(
        max_length=20, choices=STATUS_CHOICES, default="CONFIRMED", db_index=True
    )

    # Financial details
    total_amount = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)
    discount_amount = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)
    tax_amount = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)
    final_amount = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)
    amount_paid = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)
    balance_due = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)

    coupon_code = models.CharField(max_length=50, blank=True)
    pricing_breakdown = models.JSONField(default=dict, blank=True)
    participants = models.JSONField(default=list, blank=True)
    notes = models.TextField(blank=True)

    # Check-in and lifecycle tracking
    checked_in_at = models.DateTimeField(null=True, blank=True)
    checked_in_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="verified_checkins",
    )
    cancelled_at = models.DateTimeField(null=True, blank=True)
    cancel_reason = models.TextField(blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["customer", "date", "status"], name="booking_cust_date_idx"),
            models.Index(fields=["date", "start_time"], name="booking_date_time_idx"),
            models.Index(fields=["turf", "date"], name="booking_turf_date_idx"),
            models.Index(fields=["status", "date"], name="booking_stat_date_idx"),
        ]

    def __str__(self):
        return f"{self.booking_id} | {self.turf.name} | {self.date} [{self.status}]"

    # ── Booking Status State Machine ────────────────────────────────────
    # Defines every legal status transition. Any transition not listed here
    # is rejected, preventing invalid state corruption across all codepaths.
    VALID_TRANSITIONS = {
        "UPCOMING":         {"CONFIRMED", "CANCELLED", "PAYMENT_PENDING"},
        "PAYMENT_PENDING":  {"CONFIRMED", "CANCELLED"},
        "CONFIRMED":        {"CHECKED_IN", "CANCELLED", "NO_SHOW"},
        "CHECKED_IN":       {"IN_PROGRESS", "COMPLETED"},
        "IN_PROGRESS":      {"COMPLETED"},
        "COMPLETED":        {"REFUNDED"},
        "CANCELLED":        {"REFUNDED"},
        "NO_SHOW":          set(),  # Terminal state
        "REFUNDED":         set(),  # Terminal state
    }

    def can_transition_to(self, new_status: str) -> bool:
        """Check if transitioning to new_status is allowed from current status."""
        allowed = self.VALID_TRANSITIONS.get(self.status, set())
        return new_status in allowed

    def transition_to(self, new_status: str):
        """
        Transition booking to a new status, enforcing the state machine.
        Raises ValueError if the transition is not allowed.
        Does NOT call save() — caller is responsible for saving.
        """
        if new_status == self.status:
            return  # No-op for idempotent calls
        allowed = self.VALID_TRANSITIONS.get(self.status, set())
        if new_status not in allowed:
            raise ValueError(
                f"Invalid booking status transition: {self.status} → {new_status}. "
                f"Allowed transitions from {self.status}: {sorted(allowed) if allowed else 'none (terminal state)'}."
            )
        self.status = new_status

    @classmethod
    def generate_booking_id(cls, date_obj=None):
        if not date_obj:
            date_obj = timezone.now().date()
        year_suffix = date_obj.strftime("%y")  # e.g., '26'
        random_str = uuid.uuid4().hex[:6].upper()
        return f"FT-{year_suffix}-{random_str}"

