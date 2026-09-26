import os
import uuid
from datetime import datetime, date, timedelta, time
from decimal import Decimal
from rest_framework import status, views, permissions, generics
from rest_framework.response import Response
from rest_framework.parsers import MultiPartParser, FormParser, JSONParser
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.core.files.storage import default_storage
from django.core.files.base import ContentFile

from .models import Facility, Turf, TimeSlot
from .serializers import FacilitySerializer, TurfSerializer, TimeSlotSerializer
from accounts.permissions import IsAdmin, IsStaffOrAdmin
from pricing.engine import PricingEngine
from maintenance.models import Maintenance
from .services import SchedulingEngine


class TurfImageUploadView(views.APIView):
    permission_classes = [IsAdmin]
    parser_classes = [MultiPartParser, FormParser, JSONParser]

    def post(self, request):
        uploaded_files = request.FILES.getlist("images")
        if not uploaded_files and "image" in request.FILES:
            uploaded_files = [request.FILES["image"]]

        if not uploaded_files:
            base64_data = request.data.get("image_base64")
            if base64_data:
                import base64
                fmt, imgstr = base64_data.split(";base64,") if ";base64," in base64_data else ("", base64_data)
                ext = fmt.split("/")[-1] if fmt else "jpg"
                filename = f"turfs/{uuid.uuid4().hex}.{ext}"
                file_content = ContentFile(base64.b64decode(imgstr))
                saved_path = default_storage.save(filename, file_content)
                url = default_storage.url(saved_path)
                return Response({"urls": [url], "url": url}, status=status.HTTP_201_CREATED)
            return Response({"error": "No image files provided."}, status=status.HTTP_400_BAD_REQUEST)

        urls = []
        for file_obj in uploaded_files:
            ext = os.path.splitext(file_obj.name)[1].lower()
            if ext not in [".jpg", ".jpeg", ".png", ".webp", ".gif", ".svg"]:
                ext = ".jpg"
            filename = f"turfs/{uuid.uuid4().hex}{ext}"
            saved_path = default_storage.save(filename, file_obj)
            url = default_storage.url(saved_path)
            urls.append(url)

        return Response({"urls": urls, "url": urls[0] if urls else ""}, status=status.HTTP_201_CREATED)



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
        turfs = Turf.objects.filter(is_deleted=False).prefetch_related("facilities")
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
            # Pre-generate initial daily slots for the new arena
            SchedulingEngine.realign_future_slots(turf, days_ahead=14)
            return Response(TurfSerializer(turf).data, status=status.HTTP_201_CREATED)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


class TurfDetailView(views.APIView):
    def get_permissions(self):
        if self.request.method == "GET":
            return [permissions.AllowAny()]
        return [IsAdmin()]

    def get(self, request, pk):
        turf = get_object_or_404(Turf, pk=pk, is_deleted=False)
        return Response(TurfSerializer(turf).data)

    def put(self, request, pk):
        turf = get_object_or_404(Turf, pk=pk, is_deleted=False)
        old_start = turf.operating_hours_start
        old_end = turf.operating_hours_end
        old_duration = turf.slot_duration_minutes
        old_price = turf.base_price

        serializer = TurfSerializer(turf, data=request.data, partial=True)
        if serializer.is_valid():
            updated = serializer.save()

            # If operating hours, slot duration, or base price changed, re-align future slots
            schedule_changed = (
                updated.operating_hours_start != old_start
                or updated.operating_hours_end != old_end
                or updated.slot_duration_minutes != old_duration
                or updated.base_price != old_price
            )
            if schedule_changed:
                SchedulingEngine.realign_future_slots(updated, days_ahead=14)

            return Response(TurfSerializer(updated).data)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    def delete(self, request, pk):
        turf = get_object_or_404(Turf, pk=pk, is_deleted=False)
        from bookings.models import Booking
        from django.db import transaction

        now_dt = timezone.now()
        today = timezone.localtime(now_dt).date()
        is_hard_purge = (
            request.query_params.get("purge") == "true"
            or request.query_params.get("hard") == "true"
        )

        with transaction.atomic():
            turf_name = turf.name

            if is_hard_purge:
                # Permanent hard purge of this turf and its related records (useful for mock resets)
                Booking.objects.filter(turf=turf).delete()
                from reviews.models import Review
                Review.objects.filter(turf=turf).delete()
                turf.delete()
                return Response(
                    {"message": f"Turf '{turf_name}' and all associated records were permanently purged."},
                    status=status.HTTP_200_OK,
                )

            # Auto-cancel any active or upcoming bookings so deletion is never blocked for admin
            active_bookings = Booking.objects.filter(
                turf=turf,
                date__gte=today,
                status__in=["CONFIRMED", "CHECKED_IN", "PAYMENT_PENDING"],
            )
            cancelled_count = active_bookings.count()
            if cancelled_count > 0:
                active_bookings.update(
                    status="CANCELLED",
                    cancel_reason=f"Arena '{turf_name}' was decommissioned and removed by administrator.",
                    cancelled_at=now_dt,
                )

            # Evict all unbooked future time slots and release any held locks
            TimeSlot.objects.filter(
                turf=turf,
                date__gte=today,
                status__in=["AVAILABLE", "LOCKED"],
            ).delete()

            # Soft-delete the turf
            turf.is_deleted = True
            turf.is_active = False
            turf.deleted_at = now_dt
            turf.save(update_fields=["is_deleted", "is_active", "deleted_at"])

        msg = f"Turf '{turf_name}' has been successfully deleted."
        if cancelled_count > 0:
            msg += f" ({cancelled_count} scheduled booking(s) were automatically cancelled)."

        return Response(
            {
                "message": msg,
                "cancelled_booking_count": cancelled_count,
            },
            status=status.HTTP_200_OK,
        )


class TurfAvailabilityView(views.APIView):
    permission_classes = [permissions.AllowAny]

    def get(self, request, pk):
        turf = get_object_or_404(Turf, pk=pk, is_deleted=False)
        if not turf.is_active and not (
            request.user
            and request.user.is_authenticated
            and (
                getattr(request.user, "role", "") in ("ADMIN", "STAFF")
                or request.user.is_superuser
            )
        ):
            return Response(
                {"error": f"Turf '{turf.name}' is currently closed."},
                status=status.HTTP_404_NOT_FOUND,
            )

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

        availability_data = SchedulingEngine.get_turf_availability(
            turf=turf, date_obj=date_obj, user=request.user
        )
        return Response(availability_data)


class DailyScheduleView(views.APIView):
    permission_classes = [permissions.AllowAny]

    def get(self, request):
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

        sport = request.query_params.get("sport_type")
        turfs_qs = Turf.objects.filter(is_active=True, is_deleted=False).prefetch_related("facilities")
        if sport and sport.upper() != "ALL":
            turfs_qs = turfs_qs.filter(sport_type=sport.upper())

        pricing_context = PricingEngine.get_pricing_context(date_obj=date_obj)
        turfs_data = SchedulingEngine.get_daily_schedule_batch(
            turfs=turfs_qs,
            date_obj=date_obj,
            user=request.user,
            pricing_context=pricing_context,
        )

        return Response(
            {
                "date": str(date_obj),
                "turfs": turfs_data,
            }
        )


