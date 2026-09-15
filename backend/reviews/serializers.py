from rest_framework import serializers
from .models import Review
from accounts.serializers import UserSerializer


class ReviewSerializer(serializers.ModelSerializer):
    customer_name = serializers.ReadOnlyField(source="customer.full_name")
    turf_name = serializers.ReadOnlyField(source="turf.name")
    booking_reference = serializers.ReadOnlyField(source="booking.booking_id")

    class Meta:
        model = Review
        fields = [
            "id",
            "booking",
            "booking_reference",
            "customer",
            "customer_name",
            "turf",
            "turf_name",
            "rating",
            "facility_rating",
            "staff_rating",
            "review_text",
            "suggestions",
            "admin_response",
            "is_flagged",
            "flag_reason",
            "is_hidden",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "customer", "created_at", "updated_at"]
