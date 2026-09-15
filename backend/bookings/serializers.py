from rest_framework import serializers
from .models import Booking
from turfs.serializers import TurfSerializer, TimeSlotSerializer
from accounts.serializers import UserSerializer
from qr_system.models import QRTicket


class QRTicketSerializer(serializers.ModelSerializer):
    class Meta:
        model = QRTicket
        fields = ["ticket_code", "qr_base64", "is_used", "used_at"]


class BookingSerializer(serializers.ModelSerializer):
    turf_details = TurfSerializer(source="turf", read_only=True)
    customer_details = UserSerializer(source="customer", read_only=True)
    slots_data = TimeSlotSerializer(source="slots", many=True, read_only=True)
    qr_ticket_data = QRTicketSerializer(source="qr_ticket", read_only=True)

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
