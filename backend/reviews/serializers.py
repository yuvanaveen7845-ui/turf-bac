from rest_framework import serializers
from .models import Review
from accounts.serializers import UserSerializer


class ReviewSerializer(serializers.ModelSerializer):
    customer_name = serializers.SerializerMethodField()
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
            "guest_name",
            "guest_phone",
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
        extra_kwargs = {
            "booking": {"required": False, "allow_null": True},
            "turf": {"required": False},
        }

    def get_customer_name(self, obj):
        if obj.customer:
            return obj.customer.full_name or obj.customer.email.split("@")[0]
        return obj.guest_name or "Verified Athlete"
