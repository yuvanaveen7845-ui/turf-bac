from django.db import models
from turfs.models import Turf


class PricingRule(models.Model):
    RULE_TYPE_CHOICES = (
        ("WEEKDAY", "Weekday Standard"),
        ("WEEKEND", "Weekend Premium"),
        ("PEAK_HOUR", "Peak Hour Surcharge"),
        ("OFF_PEAK", "Off-Peak Discount"),
        ("HOLIDAY", "Holiday Pricing"),
        ("SPECIAL_EVENT", "Special Event Pricing"),
        ("PROMOTIONAL", "Promotional Discount"),
        ("LAST_MINUTE", "Last-Minute Discount"),
        ("EARLY_BOOKING", "Early-Bird Discount"),
    )

    ADJUSTMENT_CHOICES = (
        ("PERCENTAGE", "Percentage (+/- %)"),
        ("FIXED", "Fixed Amount (+/- ₹)"),
    )

    name = models.CharField(max_length=100)
    rule_type = models.CharField(max_length=30, choices=RULE_TYPE_CHOICES)
    turf = models.ForeignKey(
        Turf,
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="pricing_rules",
    )
    adjustment_type = models.CharField(
        max_length=20, choices=ADJUSTMENT_CHOICES, default="PERCENTAGE"
    )
    adjustment_value = models.DecimalField(
        max_digits=8,
        decimal_places=2,
        help_text="e.g. 20.00 for +20% or -10.00 for 10% discount",
    )

    # Scheduling criteria
    applicable_days = models.JSONField(
        default=list, blank=True, help_text="List of day integers 0=Mon, 6=Sun"
    )
    start_time = models.TimeField(
        null=True, blank=True, help_text="Start time for peak/off-peak"
    )
    end_time = models.TimeField(
        null=True, blank=True, help_text="End time for peak/off-peak"
    )
    start_date = models.DateField(null=True, blank=True)
    end_date = models.DateField(null=True, blank=True)

    priority = models.IntegerField(default=10)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-priority", "id"]

    def __str__(self):
        sign = "+" if self.adjustment_value > 0 else ""
        unit = "%" if self.adjustment_type == "PERCENTAGE" else "₹"
        return f"{self.name} ({sign}{self.adjustment_value}{unit})"


class Holiday(models.Model):
    name = models.CharField(max_length=100)
    date = models.DateField(unique=True)
    surge_multiplier = models.DecimalField(max_digits=4, decimal_places=2, default=1.20)
    description = models.CharField(max_length=255, blank=True)

    def __str__(self):
        return f"{self.name} ({self.date}) [x{self.surge_multiplier}]"


class SpecialEvent(models.Model):
    name = models.CharField(max_length=150)
    turf = models.ForeignKey(Turf, null=True, blank=True, on_delete=models.CASCADE)
    date = models.DateField()
    surge_multiplier = models.DecimalField(max_digits=4, decimal_places=2, default=1.30)
    notes = models.TextField(blank=True)

    def __str__(self):
        return f"{self.name} on {self.date}"
