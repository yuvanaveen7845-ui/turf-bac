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


from django.utils import timezone


class TimeSlotSerializer(serializers.ModelSerializer):
    is_available = serializers.SerializerMethodField()
    is_past = serializers.SerializerMethodField()
    is_ongoing = serializers.SerializerMethodField()
    slot_state = serializers.SerializerMethodField()

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
            "is_past",
            "is_ongoing",
            "slot_state",
            "locked_until",
            "booking_id",
        ]

    def _get_time_context(self):
        now = timezone.localtime(timezone.now())
        return now.date(), now.time()

    def get_is_past(self, obj):
        current_date, current_time = self._get_time_context()
        if obj.date < current_date:
            return True
        if obj.date == current_date:
            # Slot has ended or started in the past
            return obj.end_time <= current_time
        return False

    def get_is_ongoing(self, obj):
        current_date, current_time = self._get_time_context()
        if obj.date == current_date and obj.start_time <= current_time < obj.end_time:
            return True
        return False

    def get_is_available(self, obj):
        current_date, current_time = self._get_time_context()
        if obj.date < current_date:
            return False
        if obj.date == current_date and obj.start_time <= current_time:
            return False

        if obj.status == "AVAILABLE":
            return True
        if obj.status == "LOCKED" and obj.is_lock_expired():
            return True
        return False

    def get_slot_state(self, obj):
        current_date, current_time = self._get_time_context()

        # 1. Past check
        if obj.date < current_date or (obj.date == current_date and obj.end_time <= current_time):
            if obj.status == "BOOKED":
                return "COMPLETED"
            return "PAST"

        # 2. Ongoing check
        if obj.date == current_date and obj.start_time <= current_time < obj.end_time:
            return "ONGOING"

        # 3. Status checks
        if obj.status == "BOOKED":
            return "BOOKED"
        if obj.status == "MAINTENANCE":
            return "MAINTENANCE"
        if obj.status == "LOCKED" and not obj.is_lock_expired():
            return "LOCKED"

        return "AVAILABLE"
