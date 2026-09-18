from rest_framework import serializers
from .models import Coupon, CouponUsage


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
