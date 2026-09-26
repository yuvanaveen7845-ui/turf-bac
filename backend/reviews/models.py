from django.db import models
from django.conf import settings
from bookings.models import Booking
from turfs.models import Turf


class Review(models.Model):
    booking = models.OneToOneField(
        Booking, on_delete=models.CASCADE, related_name="review"
    )
    customer = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="reviews"
    )
    turf = models.ForeignKey(Turf, on_delete=models.PROTECT, related_name="reviews")

    rating = models.IntegerField(default=5, help_text="Overall rating 1-5")
    facility_rating = models.IntegerField(
        default=5, help_text="Pitch/turf condition 1-5"
    )
    staff_rating = models.IntegerField(default=5, help_text="Staff service 1-5")

    review_text = models.TextField()
    suggestions = models.TextField(blank=True)
    admin_response = models.TextField(blank=True)

    is_flagged = models.BooleanField(default=False)
    flag_reason = models.CharField(max_length=255, blank=True)
    is_hidden = models.BooleanField(default=False)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.customer.email} on {self.turf.name} - {self.rating}★"
