from django.db import models
from django.conf import settings
from django.utils import timezone


class MembershipPlan(models.Model):
    name = models.CharField(max_length=50, unique=True)
    slug = models.SlugField(max_length=50, unique=True)
    tier_level = models.IntegerField(
        default=1, help_text="1=Silver, 2=Gold, 3=Platinum"
    )
    description = models.TextField()
    discount_percentage = models.DecimalField(
        max_digits=5, decimal_places=2, default=5.00
    )
    priority_booking_days = models.IntegerField(
        default=7, help_text="Can book up to N days in advance"
    )
    loyalty_point_multiplier = models.DecimalField(
        max_digits=4, decimal_places=2, default=1.00
    )
    monthly_price = models.DecimalField(max_digits=8, decimal_places=2, default=499.00)
    annual_price = models.DecimalField(max_digits=8, decimal_places=2, default=4999.00)
    features = models.JSONField(default=list)
    badge_color = models.CharField(max_length=20, default="#10B981")
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.name} Plan ({self.discount_percentage}% off)"


class CustomerMembership(models.Model):
    STATUS_CHOICES = (
        ("ACTIVE", "Active"),
        ("EXPIRED", "Expired"),
        ("CANCELLED", "Cancelled"),
    )

    customer = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="memberships"
    )
    plan = models.ForeignKey(
        MembershipPlan, on_delete=models.CASCADE, related_name="active_subscribers"
    )
    billing_cycle = models.CharField(
        max_length=20,
        choices=(("MONTHLY", "Monthly"), ("ANNUAL", "Annual")),
        default="MONTHLY",
    )
    start_date = models.DateField(default=timezone.now)
    end_date = models.DateField()
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="ACTIVE")
    auto_renew = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.customer.email} -> {self.plan.name} [{self.status}]"
