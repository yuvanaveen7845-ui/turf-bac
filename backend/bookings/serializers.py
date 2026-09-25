from rest_framework import serializers
from .models import Booking
from turfs.serializers import TurfSerializer, TimeSlotSerializer
from accounts.serializers import UserSerializer
from qr_system.models import QRCredential
from qr_system.services import QRService


class QRCredentialSerializer(serializers.ModelSerializer):
    class Meta:
        model = QRCredential
        fields = ["credential_token", "qr_base64", "status", "valid_from", "valid_until", "checkin_at"]


class BookingSerializer(serializers.ModelSerializer):
    turf_details = TurfSerializer(source="turf", read_only=True)
    customer_details = UserSerializer(source="customer", read_only=True)
    slots_data = TimeSlotSerializer(source="slots", many=True, read_only=True)
    qr_ticket_data = serializers.SerializerMethodField()
    already_refunded = serializers.SerializerMethodField()
    refundable_amount = serializers.SerializerMethodField()

    def get_already_refunded(self, obj):
        from django.db.models import Sum
        from decimal import Decimal
        total = obj.refunds.filter(status__in=["COMPLETED", "PROCESSING"]).aggregate(Sum("amount"))["amount__sum"] or Decimal("0.00")
        return float(total)

    def get_refundable_amount(self, obj):
        paid = float(obj.amount_paid or 0)
        refunded = self.get_already_refunded(obj)
        return max(0.0, round(paid - refunded, 2))

    def get_qr_ticket_data(self, obj):
        cred = getattr(obj, "qr_credential", None)
        if not cred and obj.status in ["CONFIRMED", "UPCOMING", "CHECKED_IN"]:
            cred = QRService.generate_credential_for_booking(obj)
        if not cred:
            return None
        has_balance = float(obj.balance_due) > 0
        return {
            "ticket_code": cred.credential_token,
            "qr_base64": None if has_balance else cred.qr_base64,
            "qr_locked": has_balance,
            "is_used": cred.status == "USED" or obj.status == "CHECKED_IN",
            "status": cred.status,
        }

    class Meta:
        model = Booking
        fields = [
            "id",
            "booking_id",
            "customer",
            "customer_details",
            "turf",
            "turf_details",
            "date",
            "start_time",
            "end_time",
            "slots",
            "slots_data",
            "booking_type",
            "status",
            "total_amount",
            "discount_amount",
            "tax_amount",
            "final_amount",
            "amount_paid",
            "balance_due",
            "already_refunded",
            "refundable_amount",
            "coupon_code",
            "pricing_breakdown",
            "participants",
            "notes",
            "checked_in_at",
            "cancelled_at",
            "cancel_reason",
            "completed_at",
            "qr_ticket_data",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            "booking_id",
            "total_amount",
            "discount_amount",
            "tax_amount",
            "final_amount",
            "created_at",
            "updated_at",
        ]


class LockSlotSerializer(serializers.Serializer):
    turf_id = serializers.CharField()
    date = serializers.DateField()
    slot_ids = serializers.ListField(child=serializers.CharField())


class CreateBookingSerializer(serializers.Serializer):
    turf_id = serializers.CharField()
    date = serializers.DateField()
    slot_ids = serializers.ListField(child=serializers.CharField())
    booking_type = serializers.ChoiceField(
        choices=Booking.BOOKING_TYPE_CHOICES, default="REGULAR"
    )
    coupon_code = serializers.CharField(required=False, allow_blank=True)
    payment_type = serializers.ChoiceField(
        choices=[("FULL", "Full"), ("PARTIAL", "Partial"), ("PENDING", "Pending")],
        default="FULL",
    )
    payment_method = serializers.ChoiceField(
        choices=[
            ("UPI", "UPI"),
            ("CARD", "Card"),
            ("WALLET", "Wallet"),
            ("CASH", "Cash"),
        ],
        default="UPI",
    )
    notes = serializers.CharField(required=False, allow_blank=True)
    participants = serializers.ListField(required=False, default=list)


class CancelBookingSerializer(serializers.Serializer):
    reason = serializers.CharField(required=False, allow_blank=True)


class RescheduleBookingSerializer(serializers.Serializer):
    new_date = serializers.DateField()
    new_slot_ids = serializers.ListField(child=serializers.CharField())
