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

    min_price = serializers.SerializerMethodField()
    max_price = serializers.SerializerMethodField()

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
            "min_price",
            "max_price",
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
        read_only_fields = ["id", "rating", "total_reviews", "min_price", "max_price", "created_at", "updated_at"]

    def _get_range(self, obj):
        range_data = getattr(obj, "_price_range", None)
        if range_data is None:
            from pricing.engine import PricingEngine
            range_data = PricingEngine.get_turf_price_range(obj)
            obj._price_range = range_data
        return range_data

    def get_min_price(self, obj):
        return self._get_range(obj).get("min_price", float(obj.base_price))

    def get_max_price(self, obj):
        return self._get_range(obj).get("max_price", float(obj.base_price))

    def validate_images(self, value):
        if value is None:
            return []
        if not isinstance(value, list):
            raise serializers.ValidationError("Images must be provided as a list.")
        for item in value:
            if not isinstance(item, str):
                continue
            if item.startswith("data:image") or len(item) > 2000:
                raise serializers.ValidationError(
                    "Raw base64 images cannot be saved directly into turf records. Please upload them as image files."
                )
        return value


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
        """
        Returns (current_date, current_time) — computed once per serialization batch.
        Callers pass 'now_context' via serializer context to avoid repeated
        timezone.localtime() calls (was 4× per slot × 68 slots = 272 calls).
        """
        ctx = self.context.get("now_context")
        if ctx:
            return ctx
        # Fallback: compute once and cache on instance for this batch
        if not hasattr(self, "_cached_time_context"):
            now = timezone.localtime(timezone.now())
            self._cached_time_context = (now.date(), now.time())
        return self._cached_time_context

    def get_is_past(self, obj):
        current_date, current_time = self._get_time_context()
        if obj.date < current_date:
            return True
        if obj.date == current_date:
            if obj.end_time <= obj.start_time:
                # Slot spans midnight and concludes tomorrow at 00:00 (not in the past today)
                return False
            return obj.end_time <= current_time
        return False

    def get_is_ongoing(self, obj):
        current_date, current_time = self._get_time_context()
        if obj.date == current_date:
            if obj.end_time <= obj.start_time:
                return current_time >= obj.start_time
            return obj.start_time <= current_time < obj.end_time
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

        # 1. Past check (robust for midnight rollover)
        spans_midnight = (obj.end_time <= obj.start_time)
        is_past = (
            obj.date < current_date
            or (obj.date == current_date and not spans_midnight and obj.end_time <= current_time)
        )
        if is_past:
            if obj.status == "BOOKED":
                return "COMPLETED"
            return "PAST"

        # 2. Ongoing check
        is_ongoing = False
        if obj.date == current_date:
            if spans_midnight:
                is_ongoing = (current_time >= obj.start_time)
            else:
                is_ongoing = (obj.start_time <= current_time < obj.end_time)

        if is_ongoing:
            return "ONGOING"

        # 3. Status checks
        if obj.status == "BOOKED":
            return "BOOKED"
        if obj.status == "MAINTENANCE":
            return "MAINTENANCE"
        if obj.status == "LOCKED" and not obj.is_lock_expired():
            return "LOCKED"

        return "AVAILABLE"
