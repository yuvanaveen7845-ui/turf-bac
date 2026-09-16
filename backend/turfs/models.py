from django.db import models
from django.conf import settings
from django.utils import timezone


class Facility(models.Model):
    name = models.CharField(max_length=100, unique=True)
    icon = models.CharField(max_length=50, default="activity")  # Lucide icon name
    description = models.CharField(max_length=255, blank=True)

    class Meta:
        verbose_name_plural = "Facilities"

    def __str__(self):
        return self.name


class Turf(models.Model):
    SPORT_CHOICES = (
        ("FOOTBALL", "Football"),
        ("CRICKET", "Box Cricket"),
        ("MULTI_SPORT", "Multi-Sport"),
        ("BADMINTON", "Badminton"),
        ("TENNIS", "Tennis"),
    )

    name = models.CharField(max_length=150)
    slug = models.SlugField(max_length=150, unique=True)
    sport_type = models.CharField(
        max_length=30, choices=SPORT_CHOICES, default="FOOTBALL"
    )
    description = models.TextField()
    location = models.CharField(max_length=150)
    address = models.TextField()
    base_price = models.DecimalField(
        max_digits=10, decimal_places=2, help_text="Base price per 1-hour slot"
    )
    capacity = models.IntegerField(default=14, help_text="Recommended max players")
    surface_spec = models.CharField(
        max_length=150,
        default="50mm FIFA Quality Pro Artificial Turf",
        help_text="Turf surface specification",
    )
    is_fifa_certified = models.BooleanField(
        default=True, help_text="FIFA certification status"
    )
    lighting_spec = models.CharField(
        max_length=150,
        default="400 Lux Anti-Glare LED Floodlights",
        help_text="Lighting specification",
    )
    dugout_spec = models.CharField(
        max_length=150,
        default="14-Player Shaded Dugout & Tactical Board",
        help_text="Dugout capacity and amenities",
    )
    dimensions = models.CharField(
        max_length=100,
        default="110ft x 70ft (7v7 Standard)",
        help_text="Pitch playing dimensions",
    )
    fast_fill_threshold = models.IntegerField(
        default=4, help_text="Remaining slots threshold for fast-fill badge"
    )
    facilities = models.ManyToManyField(Facility, blank=True, related_name="turfs")
    images = models.JSONField(default=list, help_text="List of image URLs")
    operating_hours_start = models.TimeField(default="06:00:00")
    operating_hours_end = models.TimeField(default="23:00:00")
    slot_duration_minutes = models.IntegerField(default=60)
    is_active = models.BooleanField(default=True)
    rating = models.DecimalField(max_digits=3, decimal_places=2, default=5.00)
    total_reviews = models.IntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.name} - {self.location}"


class TimeSlot(models.Model):
    STATUS_CHOICES = (
        ("AVAILABLE", "Available"),
        ("LOCKED", "Temporarily Locked"),
        ("BOOKED", "Booked"),
        ("MAINTENANCE", "Under Maintenance"),
    )

    turf = models.ForeignKey(Turf, on_delete=models.CASCADE, related_name="slots")
    date = models.DateField(db_index=True)
    start_time = models.TimeField()
    end_time = models.TimeField()
    status = models.CharField(
        max_length=20, choices=STATUS_CHOICES, default="AVAILABLE"
    )
    price = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)

    # Temporary lock mechanism
    locked_until = models.DateTimeField(null=True, blank=True)
    locked_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="locked_slots",
    )
    booking_id = models.CharField(max_length=50, blank=True)

    class Meta:
        ordering = ["date", "start_time"]
        indexes = [
            models.Index(fields=["turf", "date", "status"]),
        ]

    def __str__(self):
        return f"{self.turf.name} | {self.date} {self.start_time.strftime('%H:%M')}-{self.end_time.strftime('%H:%M')} [{self.status}]"

    def is_lock_expired(self):
        if self.status == "LOCKED" and self.locked_until:
            return timezone.now() > self.locked_until
        return False
