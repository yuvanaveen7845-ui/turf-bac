from rest_framework import serializers
from .models import PricingRule, Holiday, SpecialEvent
from turfs.serializers import TurfSerializer


class PricingRuleSerializer(serializers.ModelSerializer):
    turf_name = serializers.ReadOnlyField(source="turf.name")

    class Meta:
        model = PricingRule
        fields = [
            "id",
            "name",
            "rule_type",
            "turf",
            "turf_name",
            "adjustment_type",
            "adjustment_value",
            "applicable_days",
            "start_time",
            "end_time",
            "start_date",
            "end_date",
            "priority",
            "is_active",
            "created_at",
        ]


class HolidaySerializer(serializers.ModelSerializer):
    class Meta:
        model = Holiday
        fields = ["id", "name", "date", "surge_multiplier", "description"]


class SpecialEventSerializer(serializers.ModelSerializer):
    turf_name = serializers.ReadOnlyField(source="turf.name")

    class Meta:
        model = SpecialEvent
        fields = [
            "id",
            "name",
            "turf",
            "turf_name",
            "date",
            "surge_multiplier",
            "notes",
        ]
