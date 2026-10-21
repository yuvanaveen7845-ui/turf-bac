from django.db import models
from django.conf import settings
from bookings.models import Booking
from turfs.models import Turf


class Review(models.Model):
    booking = models.ForeignKey(
        Booking, on_delete=models.SET_NULL, null=True, blank=True, related_name="reviews"
    )
    customer = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="reviews"
    )
    turf = models.ForeignKey(Turf, on_delete=models.PROTECT, related_name="reviews")

    guest_name = models.CharField(max_length=150, blank=True)
    guest_phone = models.CharField(max_length=20, blank=True)

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
        user_ident = (self.customer.email if self.customer else self.guest_name) or "Player"
        return f"{user_ident} on {self.turf.name} - {self.rating}★"
