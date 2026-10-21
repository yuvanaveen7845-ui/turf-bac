from django.db import models
from django.conf import settings


class Notification(models.Model):
    TYPE_CHOICES = (
        ("BOOKING_CONFIRMED", "Booking Confirmed"),
        ("PAYMENT_SUCCESS", "Payment Successful"),
        ("PAYMENT_PENDING", "Payment Pending"),
        ("BOOKING_CANCELLED", "Booking Cancelled"),
        ("BOOKING_RESCHEDULED", "Booking Rescheduled"),
        ("REFUND_PROCESSED", "Refund Processed"),
        ("UPCOMING_REMINDER", "Upcoming Match Reminder"),
        ("REVIEW_REQUEST", "Review Request"),
        ("COUPON_ALERT", "Offer / Coupon Alert"),
        ("MEMBERSHIP_ALERT", "Membership Update"),
        ("SYSTEM_ALERT", "System Alert"),
    )

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="notifications"
    )
    notification_type = models.CharField(max_length=30, choices=TYPE_CHOICES)
    title = models.CharField(max_length=150)
    message = models.TextField()
    data = models.JSONField(default=dict, blank=True)
    is_read = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["user", "-created_at"], name="notif_user_created_idx"),
            models.Index(fields=["user", "is_read"], name="notif_user_read_idx"),
        ]

    def __str__(self):
        return (
            f"{self.user.email} | {self.title} [{'READ' if self.is_read else 'UNREAD'}]"
        )
