from rest_framework import serializers
from .models import MembershipPlan, CustomerMembership


class MembershipPlanSerializer(serializers.ModelSerializer):
    class Meta:
        model = MembershipPlan
        fields = [
            "id",
            "name",
            "slug",
            "tier_level",
            "description",
            "discount_percentage",
            "priority_booking_days",
            "loyalty_point_multiplier",
            "monthly_price",
            "annual_price",
            "features",
            "badge_color",
            "is_active",
        ]


class CustomerMembershipSerializer(serializers.ModelSerializer):
    plan_details = MembershipPlanSerializer(source="plan", read_only=True)

    class Meta:
        model = CustomerMembership
        fields = [
            "id",
            "plan",
            "plan_details",
            "billing_cycle",
            "start_date",
            "end_date",
            "status",
            "auto_renew",
            "created_at",
        ]
        read_only_fields = ["id", "start_date", "status", "created_at"]
