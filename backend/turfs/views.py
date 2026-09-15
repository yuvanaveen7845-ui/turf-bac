from datetime import datetime, date, timedelta, time
from decimal import Decimal
from rest_framework import status, views, permissions, generics
from rest_framework.response import Response
from django.shortcuts import get_object_or_404
from django.utils import timezone

from .models import Facility, Turf, TimeSlot
from .serializers import FacilitySerializer, TurfSerializer, TimeSlotSerializer
from accounts.permissions import IsAdmin, IsStaffOrAdmin
from pricing.engine import PricingEngine
from maintenance.models import Maintenance


class FacilityListView(views.APIView):
    def get_permissions(self):
        if self.request.method == "GET":
            return [permissions.AllowAny()]
        return [IsAdmin()]

    def get(self, request):
        facilities = Facility.objects.all()
        return Response(FacilitySerializer(facilities, many=True).data)

    def post(self, request):
        serializer = FacilitySerializer(data=request.data)
        if serializer.is_valid():
            serializer.save()
            return Response(serializer.data, status=status.HTTP_201_CREATED)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


class TurfListView(views.APIView):
    def get_permissions(self):
        if self.request.method == "GET":
            return [permissions.AllowAny()]
        return [IsAdmin()]

    def get(self, request):
        turfs = Turf.objects.all()
        sport = request.query_params.get("sport_type")
        if sport:
            turfs = turfs.filter(sport_type=sport.upper())
        is_active = request.query_params.get("is_active")
        if is_active is not None:
            turfs = turfs.filter(is_active=is_active.lower() == "true")
        elif not (
            request.user
            and request.user.is_authenticated
            and (
                getattr(request.user, "role", "") == "ADMIN"
                or request.user.is_superuser
            )
        ):
            turfs = turfs.filter(is_active=True)

        serializer = TurfSerializer(turfs, many=True)
        return Response(serializer.data)

    def post(self, request):
        serializer = TurfSerializer(data=request.data)
        if serializer.is_valid():
            turf = serializer.save()
            return Response(TurfSerializer(turf).data, status=status.HTTP_201_CREATED)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


class TurfDetailView(views.APIView):
    def get_permissions(self):
        if self.request.method == "GET":
            return [permissions.AllowAny()]
        return [IsAdmin()]

    def get(self, request, pk):
        turf = get_object_or_404(Turf, pk=pk)
        return Response(TurfSerializer(turf).data)

    def put(self, request, pk):
        turf = get_object_or_404(Turf, pk=pk)
        serializer = TurfSerializer(turf, data=request.data, partial=True)
        if serializer.is_valid():
            updated = serializer.save()
            return Response(TurfSerializer(updated).data)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    def delete(self, request, pk):
        turf = get_object_or_404(Turf, pk=pk)
        turf.is_active = False
        turf.save()
        return Response(
            {"message": f"Turf {turf.name} deactivated."}, status=status.HTTP_200_OK
        )


class TurfAvailabilityView(views.APIView):
    permission_classes = [permissions.AllowAny]

    def get(self, request, pk):
        turf = get_object_or_404(Turf, pk=pk)
        date_str = request.query_params.get("date")
        if not date_str:
            date_obj = timezone.now().date()
        else:
            try:
                date_obj = datetime.strptime(date_str, "%Y-%m-%d").date()
            except ValueError:
                return Response(
                    {"error": "Invalid date format. Use YYYY-MM-DD."},
                    status=status.HTTP_400_BAD_REQUEST,
                )

        # 1. Clean up expired locks first
        expired_slots = TimeSlot.objects.filter(
            turf=turf, date=date_obj, status="LOCKED", locked_until__lt=timezone.now()
        )
        expired_slots.update(status="AVAILABLE", locked_until=None, locked_by=None)

        # 2. Check existing slots for this date
        slots = list(
            TimeSlot.objects.filter(turf=turf, date=date_obj).order_by("start_time")
        )

        # If no slots exist yet, auto-generate standard slots for the turf's operating hours
        if not slots:
            slots = self.generate_daily_slots(turf, date_obj)

        # 3. Check for any scheduled maintenance on this date
        maintenances = Maintenance.objects.filter(
            turf=turf, date=date_obj, status__in=["SCHEDULED", "IN_PROGRESS"]
        )
        maintenance_ranges = [(m.start_time, m.end_time) for m in maintenances]

        # 4. Serialize slots with real-time dynamic pricing
        serialized_slots = []
        for slot in slots:
            # Check if falls under maintenance
            if any(
                start <= slot.start_time and end >= slot.end_time
                for start, end in maintenance_ranges
            ):
                if slot.status != "MAINTENANCE":
                    slot.status = "MAINTENANCE"
                    slot.save()

            price_info = PricingEngine.calculate_slot_price(
                turf, date_obj, slot.start_time, slot.end_time
            )
            slot_data = TimeSlotSerializer(slot).data
            slot_data["price"] = price_info["slot_price"]
            slot_data["base_price"] = price_info["base_price"]
            slot_data["applied_rules"] = price_info["applied_rules"]
            serialized_slots.append(slot_data)

        return Response(
            {
                "turf_id": str(turf.id),
                "turf_name": turf.name,
                "date": str(date_obj),
                "base_price": float(turf.base_price),
                "slots": serialized_slots,
            }
        )

    def generate_daily_slots(self, turf, date_obj):
        created_slots = []
        cur_time = turf.operating_hours_start
        end_limit = turf.operating_hours_end
        duration_minutes = turf.slot_duration_minutes

        # Loop from start to end
        while True:
            # calculate slot end time
            slot_start_dt = datetime.combine(date_obj, cur_time)
            slot_end_dt = slot_start_dt + timedelta(minutes=duration_minutes)
            slot_end_time = slot_end_dt.time()

            if slot_end_time > end_limit and slot_end_dt.date() == date_obj:
                break

            slot, _ = TimeSlot.objects.get_or_create(
                turf=turf,
                date=date_obj,
                start_time=cur_time,
                end_time=slot_end_time,
                defaults={"status": "AVAILABLE", "price": turf.base_price},
            )
            created_slots.append(slot)

            if slot_end_time >= end_limit or slot_end_dt.date() > date_obj:
                break
            cur_time = slot_end_time

        return created_slots
