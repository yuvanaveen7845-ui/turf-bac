import uuid
import re
import logging
from datetime import datetime, date
from rest_framework import status, views, permissions
from rest_framework.response import Response
from django.shortcuts import get_object_or_404
from django.utils import timezone

logger = logging.getLogger(__name__)

from .models import Booking
from turfs.models import Turf, TimeSlot
from .serializers import (
    BookingSerializer,
    CreateBookingSerializer,
    LockSlotSerializer,
    CancelBookingSerializer,
    RescheduleBookingSerializer,
)
from .services import BookingEngine
from pricing.engine import PricingEngine
from promotions.models import Coupon
from accounts.models import User
from accounts.permissions import IsStaffOrAdmin, IsAdmin
from realtime.events import publish_event


class PricePreviewView(views.APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        turf_id = request.data.get("turf_id")
        date_str = request.data.get("date")
        slot_ids = request.data.get("slot_ids", [])
        coupon_code = request.data.get("coupon_code", "").strip()

        if not turf_id or not date_str or not slot_ids:
            return Response(
                {"error": "turf_id, date, and slot_ids are required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        turf = get_object_or_404(Turf, pk=turf_id)
        try:
            date_obj = datetime.strptime(date_str, "%Y-%m-%d").date()
        except ValueError:
            return Response(
                {"error": "Invalid date format."}, status=status.HTTP_400_BAD_REQUEST
            )

        slots = TimeSlot.objects.filter(id__in=slot_ids, turf=turf, date=date_obj)
        slot_items = [
            {"start_time": s.start_time, "end_time": s.end_time} for s in slots
        ]

        coupon = None
        coupon_error = None
        if coupon_code:
            coupon = Coupon.objects.filter(code__iexact=coupon_code).first()
            if not coupon:
                coupon_error = "Invalid coupon code."
            else:
                # Preview coupon validity
                valid, msg = coupon.is_valid_for_user(
                    request.user, float(turf.base_price) * len(slots)
                )
                if not valid:
                    coupon_error = msg
                    coupon = None

        breakdown = PricingEngine.calculate_booking_total(
            turf=turf,
            date_obj=date_obj,
            slot_items=slot_items,
            coupon=coupon,
            user=request.user,
        )
        breakdown["coupon_error"] = coupon_error
        return Response(breakdown)


class LockSlotView(views.APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        serializer = LockSlotSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        data = serializer.validated_data
        turf = get_object_or_404(Turf, pk=data["turf_id"])

        success, result = BookingEngine.lock_slots(
            turf=turf,
            date_obj=data["date"],
            slot_ids=data["slot_ids"],
            user=request.user,
        )
        if not success:
            return Response({"error": result}, status=status.HTTP_409_CONFLICT)

        # Broadcast live slot lock to all connected clients
        publish_event(
            channel="slots",
            event_type="SLOT_LOCKED",
            payload={
                "turf_id": str(turf.id),
                "date": str(data["date"]),
                "slot_ids": [str(s) for s in data["slot_ids"]],
                "locked_until": result.get("locked_until") if isinstance(result, dict) else None,
            },
        )

        lock_mins = int(result.get("lock_duration_seconds", 300) / 60) if isinstance(result, dict) else 5
        return Response(
            {
                "message": f"Slots temporarily reserved for {lock_mins} minutes.",
                "locked_until": result.get("locked_until"),
                "expires_at": result.get("expires_at"),
                "lock_duration_seconds": result.get("lock_duration_seconds", 300),
                "slot_ids": result.get("slot_ids", []),
                "locked_slots": result.get("locked_slots", []),
                "data": result,
            },
            status=status.HTTP_200_OK,
        )


class UnlockSlotView(views.APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        serializer = LockSlotSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        data = serializer.validated_data
        turf = get_object_or_404(Turf, pk=data["turf_id"])

        released_count = BookingEngine.unlock_slots(
            turf=turf,
            date_obj=data["date"],
            slot_ids=data["slot_ids"],
            user=request.user,
        )

        if released_count > 0:
            publish_event(
                channel="slots",
                event_type="SLOT_RELEASED",
                payload={
                    "turf_id": str(turf.id),
                    "date": str(data["date"]),
                    "slot_ids": [str(s) for s in data["slot_ids"]],
                },
            )

        return Response(
            {
                "message": f"Successfully released {released_count} slot hold(s).",
                "released_count": released_count,
            },
            status=status.HTTP_200_OK,
        )


class BookingListCreateView(views.APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        user = request.user
        tab = request.query_params.get("tab", "upcoming")  # upcoming or past

        if user.role in ("STAFF", "ADMIN") or user.is_superuser:
            bookings = (
                Booking.objects.all()
                .select_related("turf", "customer")
                .prefetch_related("slots")
            )
            # Optional filters
            turf_filter = request.query_params.get("turf_id")
            if turf_filter:
                bookings = bookings.filter(turf_id=turf_filter)
            status_filter = request.query_params.get("status")
            if status_filter:
                bookings = bookings.filter(status=status_filter.upper())
            date_filter = request.query_params.get("date")
            if date_filter:
                bookings = bookings.filter(date=date_filter)
        else:
            bookings = (
                Booking.objects.filter(customer=user)
                .select_related("turf")
                .prefetch_related("slots")
            )
            today = timezone.now().date()
            if tab == "upcoming":
                bookings = bookings.filter(
                    date__gte=today,
                    status__in=[
                        "CONFIRMED",
                        "UPCOMING",
                        "PAYMENT_PENDING",
                        "CHECKED_IN",
                        "IN_PROGRESS",
                    ],
                )
            else:
                bookings = bookings.filter(
                    status__in=["COMPLETED", "CANCELLED", "NO_SHOW", "REFUNDED"]
                )

        serializer = BookingSerializer(bookings, many=True)
        return Response(serializer.data)

    def post(self, request):
        serializer = CreateBookingSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        data = serializer.validated_data
        turf = get_object_or_404(Turf, pk=data["turf_id"])

        try:
            booking = BookingEngine.create_booking(
                turf=turf,
                date_obj=data["date"],
                slot_ids=data["slot_ids"],
                user=request.user,
                booking_type=data.get("booking_type", "REGULAR"),
                coupon_code=data.get("coupon_code"),
                payment_type=data.get("payment_type", "FULL"),
                payment_method=data.get("payment_method", "UPI"),
                notes=data.get("notes", ""),
                participants=data.get("participants", []),
            )
            # Broadcast booking confirmed
            publish_event(
                channel="slots",
                event_type="BOOKING_CONFIRMED",
                payload={
                    "booking_id": booking.booking_id,
                    "turf_id": str(turf.id),
                    "date": str(booking.date),
                    "slot_ids": [str(s.id) for s in booking.slots.all()],
                    "customer_name": booking.customer.full_name or booking.customer.email,
                    "amount_paid": float(booking.amount_paid),
                },
            )
            publish_event(
                channel="operations",
                event_type="OPERATIONS_UPDATE",
                payload={
                    "type": "NEW_BOOKING",
                    "booking_id": booking.booking_id,
                    "turf_name": turf.name,
                    "amount": float(booking.amount_paid),
                },
            )
            return Response(
                BookingSerializer(booking).data, status=status.HTTP_201_CREATED
            )
        except ValueError as e:
            return Response({"error": str(e)}, status=status.HTTP_400_BAD_REQUEST)


class BookingDetailView(views.APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, identifier):
        # Allow lookup by id or booking_id
        booking = Booking.objects.filter(booking_id=identifier).first()
        if not booking:
            booking = Booking.objects.filter(pk=identifier).first()
        if not booking:
            return Response(
                {"error": "Booking not found."}, status=status.HTTP_404_NOT_FOUND
            )

        # Check access permission
        if request.user.role == "CUSTOMER" and booking.customer != request.user:
            return Response(
                {"error": "Unauthorized access to this booking."},
                status=status.HTTP_403_FORBIDDEN,
            )

        return Response(BookingSerializer(booking).data)


class CancelBookingView(views.APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, identifier):
        booking = get_object_or_404(Booking, booking_id=identifier)
        if request.user.role == "CUSTOMER" and booking.customer != request.user:
            return Response(
                {"error": "Unauthorized."}, status=status.HTTP_403_FORBIDDEN
            )

        serializer = CancelBookingSerializer(data=request.data)
        serializer.is_valid()
        reason = serializer.validated_data.get(
            "reason", "Customer requested cancellation"
        )

        try:
            slot_ids = [str(s.id) for s in booking.slots.all()]
            turf_id = str(booking.turf_id)
            booking_date = str(booking.date)
            updated_booking = BookingEngine.cancel_booking(
                booking, request.user, reason
            )
            # Broadcast slot released
            publish_event(
                channel="slots",
                event_type="SLOT_RELEASED",
                payload={
                    "turf_id": turf_id,
                    "date": booking_date,
                    "slot_ids": slot_ids,
                    "booking_id": booking.booking_id,
                },
            )
            return Response(BookingSerializer(updated_booking).data)
        except ValueError as e:
            return Response({"error": str(e)}, status=status.HTTP_400_BAD_REQUEST)


class RescheduleBookingView(views.APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, identifier):
        booking = get_object_or_404(Booking, booking_id=identifier)
        if request.user.role == "CUSTOMER" and booking.customer != request.user:
            return Response(
                {"error": "Unauthorized."}, status=status.HTTP_403_FORBIDDEN
            )

        serializer = RescheduleBookingSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        data = serializer.validated_data
        try:
            rescheduled = BookingEngine.reschedule_booking(
                booking=booking,
                new_date=data["new_date"],
                new_slot_ids=data["new_slot_ids"],
                user=request.user,
            )
            return Response(BookingSerializer(rescheduled).data)
        except ValueError as e:
            return Response({"error": str(e)}, status=status.HTTP_400_BAD_REQUEST)


class StaffTodayBookingsView(views.APIView):
    permission_classes = [IsStaffOrAdmin]

    def get(self, request):
        today = timezone.now().date()
        bookings = Booking.objects.filter(date=today).order_by("start_time")
        return Response(BookingSerializer(bookings, many=True).data)


class StaffWalkInBookingView(views.APIView):
    permission_classes = [IsStaffOrAdmin]

    def post(self, request):
        turf_id = request.data.get("turf_id")
        slot_ids = request.data.get("slot_ids", [])
        customer_name = (request.data.get("customer_name") or "Walk-in Guest").strip()
        customer_phone = (request.data.get("customer_phone") or "").strip()
        notes = request.data.get("notes", "")
        raw_payment_method = request.data.get("payment_method", "CASH").upper()
        if raw_payment_method not in ["CASH", "UPI", "CARD", "WALLET", "NET_BANKING", "RAZORPAY"]:
            payment_method = "CASH"
        else:
            payment_method = raw_payment_method

        turf = get_object_or_404(Turf, pk=turf_id)

        # Resolve date: from parameter, or inferred from slot, or today
        date_str = request.data.get("date")
        if date_str:
            try:
                date_obj = datetime.strptime(date_str, "%Y-%m-%d").date()
            except (ValueError, TypeError):
                date_obj = timezone.now().date()
        elif slot_ids:
            first_slot = TimeSlot.objects.filter(id__in=slot_ids).first()
            date_obj = first_slot.date if first_slot else timezone.now().date()
        else:
            date_obj = timezone.now().date()

        # Find or create customer account
        guest_user = None
        if customer_phone:
            clean_phone = User.canonicalize_phone(customer_phone)
            guest_user = User.objects.filter(phone=clean_phone).first() or User.objects.filter(phone=customer_phone).first()

        if not guest_user:
            slug = re.sub(r"[^\d]", "", customer_phone) if customer_phone else timezone.now().strftime("%Y%m%d%H%M%S")
            email = f"walkin_{slug}_{uuid.uuid4().hex[:4]}@friendsturf.local"
            guest_user = User.objects.create(
                email=email,
                first_name=customer_name or "Walk-in Guest",
                phone=customer_phone,
                role="CUSTOMER",
            )
        elif customer_name and guest_user.first_name in ("Walk-in Guest", ""):
            guest_user.first_name = customer_name
            guest_user.save()

        # Walk-in via Razorpay: Create a PAYMENT_PENDING booking with server-calculated order
        if payment_method == "RAZORPAY":
            from payments.razorpay_client import RazorpayService
            from payments.models import Payment

            try:
                booking = BookingEngine.create_booking(
                    turf=turf,
                    date_obj=date_obj,
                    slot_ids=slot_ids,
                    user=guest_user,
                    booking_type="WALK_IN",
                    payment_type="PENDING",
                    payment_method="RAZORPAY",
                    notes=f"Walk-in digital checkout initiated by staff {request.user.email}. {notes}".strip(),
                    collected_by=request.user,
                )

                rzp_order = RazorpayService.create_order(
                    amount_in_rupees=booking.final_amount,
                    receipt_id=booking.booking_id,
                    notes={
                        "booking_id": booking.booking_id,
                        "type": "WALK_IN_RAZORPAY",
                        "customer_name": customer_name,
                        "turf_name": turf.name,
                    },
                )

                payment_id = Payment.generate_payment_id()
                payment = Payment.objects.create(
                    payment_id=payment_id,
                    booking=booking,
                    customer=guest_user,
                    provider="RAZORPAY",
                    provider_order_id=rzp_order["order_id"],
                    amount=booking.final_amount,
                    currency="INR",
                    payment_method="UPI",
                    payment_type="FULL",
                    transaction_reference=f"TXN-{booking.booking_id}-{uuid.uuid4().hex[:6].upper()}",
                    status="PENDING",
                    collected_by=request.user,
                    notes="Walk-in desk Razorpay intent",
                )

                return Response(
                    {
                        "booking": BookingSerializer(booking).data,
                        "booking_id": booking.booking_id,
                        "order_id": rzp_order["order_id"],
                        "amount": rzp_order["amount"],
                        "currency": rzp_order["currency"],
                        "key_id": rzp_order["key_id"],
                        "amount_to_pay": float(booking.final_amount),
                        "payment_id": payment.payment_id,
                    },
                    status=status.HTTP_201_CREATED,
                )
            except ValueError as e:
                return Response({"error": str(e)}, status=status.HTTP_400_BAD_REQUEST)
            except Exception as e:
                logger.exception("Walk-in Razorpay error")
                return Response(
                    {
                        "error": "Payment gateway initialization failed for walk-in booking.",
                        "code": "PAYMENT_PROVIDER_UNAVAILABLE",
                    },
                    status=status.HTTP_502_BAD_GATEWAY,
                )

        # Walk-in via Offline (Cash / Counter UPI / Card / Wallet)
        payment_type = request.data.get("payment_type", "FULL")
        if payment_type not in ["FULL", "PARTIAL", "PENDING", "ON_ARRIVAL"]:
            payment_type = "FULL"
        if payment_type == "ON_ARRIVAL":
            payment_type = "PENDING"

        try:
            booking = BookingEngine.create_booking(
                turf=turf,
                date_obj=date_obj,
                slot_ids=slot_ids,
                user=guest_user,
                booking_type="WALK_IN",
                payment_type=payment_type,
                payment_method=payment_method,
                notes=f"Walk-in booked by staff {request.user.email}. {notes}".strip(),
                collected_by=request.user,
            )
            # Broadcast walk-in confirmed
            publish_event(
                channel="slots",
                event_type="BOOKING_CONFIRMED",
                payload={
                    "booking_id": booking.booking_id,
                    "turf_id": str(turf.id),
                    "date": str(booking.date),
                    "slot_ids": [str(s.id) for s in booking.slots.all()],
                    "customer_name": customer_name,
                    "amount_paid": float(booking.amount_paid),
                },
            )
            publish_event(
                channel="operations",
                event_type="OPERATIONS_UPDATE",
                payload={
                    "type": "WALK_IN_CREATED",
                    "booking_id": booking.booking_id,
                    "turf_name": turf.name,
                    "amount": float(booking.amount_paid),
                },
            )
            return Response(
                BookingSerializer(booking).data, status=status.HTTP_201_CREATED
            )
        except ValueError as e:
            return Response({"error": str(e)}, status=status.HTTP_400_BAD_REQUEST)
        except Exception as e:
            logger.exception("Walk-in booking error")
            return Response(
                {"error": f"Failed to complete walk-in booking: {str(e)}"},
                status=status.HTTP_400_BAD_REQUEST,
            )


class RecordOfflinePaymentView(views.APIView):
    """
    Allows Staff/Admin to quickly record an offline payment (Cash, UPI, Card)
    against a booking in under 10 seconds.
    """
    permission_classes = [IsStaffOrAdmin]

    def post(self, request, identifier):
        booking = Booking.objects.filter(booking_id=identifier).first()
        if not booking:
            booking = get_object_or_404(Booking, pk=identifier)

        amount = request.data.get("amount")
        payment_method = request.data.get("payment_method", "CASH").upper()
        reference_id = request.data.get("reference_id", "").strip()

        if not amount:
            return Response(
                {"error": "Payment amount is required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            payment = BookingEngine.record_offline_payment(
                booking=booking,
                amount=amount,
                payment_method=payment_method,
                reference_id=reference_id,
                collected_by=request.user,
            )

            # Broadcast operations update
            publish_event(
                channel="operations",
                event_type="OPERATIONS_UPDATE",
                payload={
                    "type": "PAYMENT_RECORDED",
                    "booking_id": booking.booking_id,
                    "amount": float(payment.amount),
                    "method": payment_method,
                },
            )

            return Response(
                {
                    "message": f"Payment of ₹{payment.amount} recorded successfully.",
                    "booking": BookingSerializer(booking).data,
                },
                status=status.HTTP_200_OK,
            )
        except ValueError as e:
            return Response({"error": str(e)}, status=status.HTTP_400_BAD_REQUEST)

