from rest_framework import serializers
from .models import Coupon, CouponUsage, ReferralReward


class CouponSerializer(serializers.ModelSerializer):
    class Meta:
        model = Coupon
        fields = [
            "id",
            "code",
            "title",
            "description",
            "discount_type",
            "discount_value",
            "min_booking_amount",
            "max_discount_amount",
            "start_date",
            "end_date",
            "usage_limit",
            "per_user_limit",
            "usage_count",
            "coupon_type",
            "is_active",
            "created_at",
        ]
        read_only_fields = ["id", "usage_count", "created_at"]


class ReferralRewardSerializer(serializers.ModelSerializer):
    referred_email = serializers.ReadOnlyField(source="referred_user.email")
    referred_name = serializers.ReadOnlyField(source="referred_user.full_name")

    class Meta:
        model = ReferralReward
        fields = [
            "id",
            "referred_email",
            "referred_name",
            "reward_amount",
            "status",
            "created_at",
            "credited_at",
        ]
