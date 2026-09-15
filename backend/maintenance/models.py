from django.db import models
from django.conf import settings
from turfs.models import Turf


class Maintenance(models.Model):
    STATUS_CHOICES = (
        ("SCHEDULED", "Scheduled"),
        ("IN_PROGRESS", "In Progress"),
        ("COMPLETED", "Completed"),
        ("CANCELLED", "Cancelled"),
    )

    turf = models.ForeignKey(
        Turf, on_delete=models.CASCADE, related_name="maintenance_schedules"
    )
    date = models.DateField(db_index=True)
    start_time = models.TimeField()
    end_time = models.TimeField()
    reason = models.CharField(
        max_length=200, help_text="e.g., Turf grooming, floodlight bulb replacement"
    )
    assigned_staff = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="assigned_maintenance",
    )
    status = models.CharField(
        max_length=20, choices=STATUS_CHOICES, default="SCHEDULED"
    )
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["date", "start_time"]

    def __str__(self):
        return f"Maintenance: {self.turf.name} on {self.date} ({self.start_time}-{self.end_time}) [{self.status}]"
