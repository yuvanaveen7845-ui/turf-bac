from rest_framework import status, views, generics, permissions
from rest_framework.response import Response
from django.shortcuts import get_object_or_404

from .models import Maintenance
from .serializers import MaintenanceSerializer
from turfs.models import TimeSlot
from accounts.permissions import IsStaffOrAdmin


class MaintenanceListCreateView(generics.ListCreateAPIView):
    queryset = Maintenance.objects.all().order_by("-date", "-start_time")
    serializer_class = MaintenanceSerializer
    permission_classes = [IsStaffOrAdmin]

    def perform_create(self, serializer):
        instance = serializer.save()
        # Automatically mark any matching time slots as MAINTENANCE
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
