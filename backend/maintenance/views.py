from rest_framework import status, views, generics, permissions
from rest_framework.response import Response
from django.shortcuts import get_object_or_404
from django.utils import timezone

from .models import Maintenance
from .serializers import MaintenanceSerializer
from turfs.models import Turf, TimeSlot
from bookings.models import Booking
from bookings.serializers import BookingSerializer
from accounts.permissions import IsStaffOrAdmin
from audit.models import AuditLog


class MaintenanceListCreateView(generics.ListCreateAPIView):
    queryset = Maintenance.objects.all().order_by("-date", "-start_time")
    serializer_class = MaintenanceSerializer
    permission_classes = [IsStaffOrAdmin]

    def create(self, request, *args, **kwargs):
        turf_id = request.data.get("turf")
        date_val = request.data.get("date")
        start_time_val = request.data.get("start_time")
        end_time_val = request.data.get("end_time")
        force_override = request.data.get("force_override", False)

        # Check for conflicting bookings in this window
        conflicting_slots = TimeSlot.objects.filter(
            turf_id=turf_id,
            date=date_val,
            start_time__gte=start_time_val,
            end_time__lte=end_time_val,
            status="BOOKED",
        )

        conflicting_bookings_ids = [s.booking_id for s in conflicting_slots if s.booking_id]
        affected_bookings = Booking.objects.filter(booking_id__in=conflicting_bookings_ids, status__in=["CONFIRMED", "UPCOMING"])

        if affected_bookings.exists() and not force_override:
            return Response({
                "error": "CONFIRMED_BOOKINGS_CONFLICT",
                "message": f"There are {affected_bookings.count()} confirmed bookings during this maintenance window.",
                "affected_bookings": BookingSerializer(affected_bookings, many=True).data,
                "resolution_options": ["RESCHEDULE", "CANCEL_AND_REFUND", "FORCE_OVERRIDE"],
            }, status=status.HTTP_409_CONFLICT)

        response = super().create(request, *args, **kwargs)

        AuditLog.objects.create(
            user=request.user,
            action="MAINTENANCE_SCHEDULED",
            resource_type="MAINTENANCE",
            resource_id=str(response.data.get("id")),
            details={
                "turf_id": turf_id,
                "date": date_val,
                "time": f"{start_time_val} - {end_time_val}",
                "affected_bookings_count": affected_bookings.count(),
            },
        )
        return response

    def perform_create(self, serializer):
        instance = serializer.save()
        # Mark matching time slots as MAINTENANCE
        slots = TimeSlot.objects.filter(
            turf=instance.turf,
            date=instance.date,
            start_time__gte=instance.start_time,
            end_time__lte=instance.end_time,
        ).exclude(status="BOOKED")
        slots.update(status="MAINTENANCE")


class MaintenanceDetailView(generics.RetrieveUpdateDestroyAPIView):
    queryset = Maintenance.objects.all()
    serializer_class = MaintenanceSerializer
    permission_classes = [IsStaffOrAdmin]

    def perform_destroy(self, instance):
        # Free slots back to AVAILABLE if not booked
        slots = TimeSlot.objects.filter(
            turf=instance.turf,
            date=instance.date,
            start_time__gte=instance.start_time,
            end_time__lte=instance.end_time,
            status="MAINTENANCE",
        )
        slots.update(status="AVAILABLE")
        instance.delete()


class MaintenanceConflictCheckView(views.APIView):
    """
    Dry-run check for affected bookings before scheduling maintenance.
    """
    permission_classes = [IsStaffOrAdmin]

    def post(self, request):
        turf_id = request.data.get("turf_id")
        date_val = request.data.get("date")
        start_time_val = request.data.get("start_time")
        end_time_val = request.data.get("end_time")

        if not turf_id or not date_val or not start_time_val or not end_time_val:
            return Response({"error": "All fields required."}, status=status.HTTP_400_BAD_REQUEST)

        conflicting_slots = TimeSlot.objects.filter(
            turf_id=turf_id,
            date=date_val,
            start_time__gte=start_time_val,
            end_time__lte=end_time_val,
            status="BOOKED",
        )
        conflicting_ids = [s.booking_id for s in conflicting_slots if s.booking_id]
        affected = Booking.objects.filter(booking_id__in=conflicting_ids, status__in=["CONFIRMED", "UPCOMING"])

        return Response({
            "has_conflicts": affected.exists(),
            "conflicts_count": affected.count(),
            "affected_bookings": BookingSerializer(affected, many=True).data,
        })

