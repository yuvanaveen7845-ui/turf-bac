from rest_framework import serializers
from .models import Facility, Turf, TimeSlot


class FacilitySerializer(serializers.ModelSerializer):
    class Meta:
        model = Facility
        fields = ["id", "name", "icon", "description"]


class TurfSerializer(serializers.ModelSerializer):
    facilities_data = FacilitySerializer(source="facilities", many=True, read_only=True)
    facility_ids = serializers.PrimaryKeyRelatedField(
        many=True,
        write_only=True,
        queryset=Facility.objects.all(),
        source="facilities",
        required=False,
    )

    class Meta:
        model = Turf
        fields = [
            "id",
            "name",
            "slug",
            "sport_type",
            "description",
            "location",
            "address",
            "base_price",
            "capacity",
            "surface_spec",
            "is_fifa_certified",
            "lighting_spec",
            "dugout_spec",
            "dimensions",
            "fast_fill_threshold",
            "facilities_data",
            "facility_ids",
            "images",
            "operating_hours_start",
            "operating_hours_end",
            "slot_duration_minutes",
            "is_active",
            "rating",
            "total_reviews",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "rating", "total_reviews", "created_at", "updated_at"]


class TimeSlotSerializer(serializers.ModelSerializer):
    is_available = serializers.SerializerMethodField()

    class Meta:
        model = TimeSlot
        fields = [
            "id",
            "turf",
            "date",
            "start_time",
            "end_time",
            "status",
            "price",
            "is_available",
            "locked_until",
            "booking_id",
        ]

    def get_is_available(self, obj):
        if obj.status == "AVAILABLE":
            return True
        if obj.status == "LOCKED" and obj.is_lock_expired():
            return True
        return False
