from rest_framework import serializers
from .models import Payment, Refund


class PaymentSerializer(serializers.ModelSerializer):
    customer_email = serializers.ReadOnlyField(source="customer.email")
    booking_reference = serializers.ReadOnlyField(source="booking.booking_id")

    class Meta:
        model = Payment
        fields = [
            "id",
            "payment_id",
            "booking",
            "booking_reference",
            "customer",
            "customer_email",
            "amount",
            "payment_method",
            "payment_type",
            "transaction_reference",
            "status",
            "gateway_response",
            "created_at",
            "completed_at",
        ]
        read_only_fields = [
            "id",
            "payment_id",
            "transaction_reference",
            "status",
            "created_at",
            "completed_at",
        ]


class RefundSerializer(serializers.ModelSerializer):
    class Meta:
        model = Refund
        fields = [
            "id",
            "refund_id",
            "payment",
            "booking",
            "amount",
            "refund_type",
            "refund_to",
            "status",
            "reason",
            "reference_id",
            "created_at",
            "completed_at",
        ]
        read_only_fields = [
            "id",
            "refund_id",
            "reference_id",
            "status",
            "created_at",
            "completed_at",
        ]
