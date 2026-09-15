from datetime import datetime, date
from rest_framework import status, views, permissions
from rest_framework.response import Response
from django.shortcuts import get_object_or_404
from django.utils import timezone

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

        return Response(
            {"message": "Slots temporarily reserved for 5 minutes.", "data": result},
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
            updated_booking = BookingEngine.cancel_booking(
                booking, request.user, reason
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
        customer_name = request.data.get("customer_name", "Walk-in Guest")
        customer_phone = request.data.get("customer_phone", "")
        notes = request.data.get("notes", "")

        turf = get_object_or_404(Turf, pk=turf_id)
        today = timezone.now().date()

        # Create temporary guest customer account if needed or assign to staff
        email = f"walkin_{customer_phone or timezone.now().strftime('%H%M%S')}@friendsturf.local"
        guest_user, _ = User.objects.get_or_create(
            email=email,
            defaults={
                "first_name": customer_name,
                "phone": customer_phone,
                "role": "CUSTOMER",
            },
        )

        try:
            booking = BookingEngine.create_booking(
                turf=turf,
                date_obj=today,
                slot_ids=slot_ids,
                user=guest_user,
                booking_type="WALK_IN",
                payment_type="FULL",
                payment_method="CASH",
                notes=f"Walk-in booked by staff {request.user.email}. {notes}",
            )
            return Response(
                BookingSerializer(booking).data, status=status.HTTP_201_CREATED
            )
        except ValueError as e:
            return Response({"error": str(e)}, status=status.HTTP_400_BAD_REQUEST)
