from rest_framework import serializers
from .models import Payment, Refund, DailyCashDrawer


class PaymentSerializer(serializers.ModelSerializer):
    customer_email = serializers.ReadOnlyField(source="customer.email")
    customer_name = serializers.SerializerMethodField()
    customer_phone = serializers.SerializerMethodField()
    booking_reference = serializers.ReadOnlyField(source="booking.booking_id")
    turf_name = serializers.ReadOnlyField(source="booking.turf.name")
    match_date = serializers.ReadOnlyField(source="booking.date")
    match_time = serializers.SerializerMethodField()
    booking_final_amount = serializers.ReadOnlyField(source="booking.final_amount")
    booking_amount_paid = serializers.ReadOnlyField(source="booking.amount_paid")
    booking_balance_due = serializers.ReadOnlyField(source="booking.balance_due")
    collected_by_email = serializers.ReadOnlyField(source="collected_by.email")
    payment_origin = serializers.SerializerMethodField()

    class Meta:
        model = Payment
        fields = [
            "id",
            "payment_id",
            "booking",
            "booking_reference",
            "turf_name",
            "match_date",
            "match_time",
            "booking_final_amount",
            "booking_amount_paid",
            "booking_balance_due",
            "customer",
            "customer_email",
            "customer_name",
            "customer_phone",
            "provider",
            "provider_order_id",
            "provider_payment_id",
            "amount",
            "currency",
            "payment_method",
            "payment_type",
            "payment_origin",
            "transaction_reference",
            "status",
            "failure_reason",
            "collected_by",
            "collected_by_email",
            "notes",
            "gateway_response",
            "created_at",
            "updated_at",
            "paid_at",
            "completed_at",
        ]
        read_only_fields = [
            "id",
            "payment_id",
            "transaction_reference",
            "created_at",
            "updated_at",
            "paid_at",
            "completed_at",
        ]

    def get_customer_name(self, obj):
        cust = obj.customer
        return cust.full_name or cust.first_name or cust.email.split("@")[0]

    def get_customer_phone(self, obj):
        cust = obj.customer
        return getattr(cust, "phone", "") or ""

    def get_match_time(self, obj):
        if obj.booking:
            return f"{obj.booking.start_time.strftime('%H:%M')} - {obj.booking.end_time.strftime('%H:%M')}"
        return ""

    def get_payment_origin(self, obj):
        if obj.provider == "RAZORPAY":
            return "Online · Razorpay"
        elif obj.provider == "WALLET":
            return "Online · Turf Wallet"
        return f"Offline · {obj.payment_method}"


class RefundSerializer(serializers.ModelSerializer):
    booking_reference = serializers.ReadOnlyField(source="booking.booking_id")
    customer_email = serializers.ReadOnlyField(source="booking.customer.email")
    customer_name = serializers.SerializerMethodField()
    initiated_by_email = serializers.ReadOnlyField(source="initiated_by.email")

    class Meta:
        model = Refund
        fields = [
            "id",
            "refund_id",
            "payment",
            "booking",
            "booking_reference",
            "customer_email",
            "customer_name",
            "provider_refund_id",
            "amount",
            "refund_type",
            "refund_to",
            "status",
            "reason",
            "reference_id",
            "initiated_by",
            "initiated_by_email",
            "created_at",
            "updated_at",
            "completed_at",
        ]
        read_only_fields = [
            "id",
            "refund_id",
            "provider_refund_id",
            "reference_id",
            "status",
            "created_at",
            "updated_at",
            "completed_at",
        ]

    def get_customer_name(self, obj):
        cust = obj.booking.customer
        return cust.full_name or cust.first_name or cust.email.split("@")[0]


class DailyCashDrawerSerializer(serializers.ModelSerializer):
    summary = serializers.SerializerMethodField()
    closed_by_email = serializers.ReadOnlyField(source="closed_by.email")

    class Meta:
        model = DailyCashDrawer
        fields = [
            "id",
            "date",
            "opening_cash",
            "actual_closing_cash",
            "status",
            "closed_by",
            "closed_by_email",
            "closed_at",
            "notes",
            "created_at",
            "updated_at",
            "summary",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]

    def get_summary(self, obj):
        return obj.get_summary()
