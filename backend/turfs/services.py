from datetime import datetime, timedelta, date, time
from decimal import Decimal
from django.db import transaction
from django.utils import timezone
from .models import Turf, TimeSlot
from pricing.engine import PricingEngine
from maintenance.models import Maintenance
from realtime.events import publish_event


class SchedulingEngine:
    """
    Authoritative Scheduling & Slot Generation Engine for Friends Turf.
    Single source of truth for:
    - Daily slot generation respecting operating hours & slot duration
    - Expired lock release & lifecycle cleanup
    - Live availability with dynamic pricing
    - Maintenance blackout integration
    """

    @classmethod
    def cleanup_expired_locks(cls, turf=None, date_obj=None):
        """
        Releases any temporary slot locks that have passed their expiration timestamp.
        """
        now = timezone.now()
        qs = TimeSlot.objects.filter(status="LOCKED", locked_until__lt=now)
        if turf:
            qs = qs.filter(turf=turf)
        if date_obj:
            qs = qs.filter(date=date_obj)

        expired_slots = list(qs)
        count = len(expired_slots)
        if count > 0:
            qs.update(status="AVAILABLE", locked_until=None, locked_by=None)
            for s in expired_slots:
                publish_event(
                    channel="slots",
                    event_type="SLOT_RELEASED",
                    payload={
                        "turf_id": str(s.turf_id),
                        "date": str(s.date),
                        "slot_ids": [str(s.id)],
                    },
                )
        return count

    @classmethod
    def generate_daily_slots(cls, turf, date_obj):
        """
        Generates standard time slots for a turf on a given date in a single bulk operation based on:
        - turf.operating_hours_start (default 06:00)
        - turf.operating_hours_end (default 23:00)
        - turf.slot_duration_minutes (default 60 mins)
        """
        cur_time = turf.operating_hours_start
        end_limit = turf.operating_hours_end
        duration_minutes = turf.slot_duration_minutes or 60

        existing_start_times = set(
            TimeSlot.objects.filter(turf=turf, date=date_obj).values_list(
                "start_time", flat=True
            )
        )

        new_slots = []
        while True:
            slot_start_dt = datetime.combine(date_obj, cur_time)
            slot_end_dt = slot_start_dt + timedelta(minutes=duration_minutes)
            slot_end_time = slot_end_dt.time()

            if slot_end_time > end_limit and slot_end_dt.date() == date_obj:
                break

            if cur_time not in existing_start_times:
                new_slots.append(
                    TimeSlot(
                        turf=turf,
                        date=date_obj,
                        start_time=cur_time,
                        end_time=slot_end_time,
                        status="AVAILABLE",
                        price=turf.base_price,
                    )
                )

            if slot_end_time >= end_limit or slot_end_dt.date() > date_obj:
                break
            cur_time = slot_end_time

        if new_slots:
            TimeSlot.objects.bulk_create(new_slots, ignore_conflicts=True)

        return list(
            TimeSlot.objects.filter(turf=turf, date=date_obj).order_by("start_time")
        )

    @classmethod
    def get_turf_availability(cls, turf, date_obj, user=None, pricing_context=None):
        """
        Computes the complete, authoritative availability of slots for a turf on date_obj.
        Accounts for:
        - Expired lock cleanup
        - Slot generation if missing (bulk)
        - Maintenance blackout periods
        - Real-time dynamic pricing per slot (batched pricing context)
        - Attached booking information for staff/admin users
        """
        # 1. Clean up expired locks first
        cls.cleanup_expired_locks(turf=turf, date_obj=date_obj)

        # 2. Fetch existing slots or generate
        slots = list(
            TimeSlot.objects.filter(turf=turf, date=date_obj).order_by("start_time")
        )
        if not slots:
            slots = cls.generate_daily_slots(turf, date_obj)

        # 3. Check for active/scheduled maintenance blackouts
        maintenances = Maintenance.objects.filter(
            turf=turf, date=date_obj, status__in=["SCHEDULED", "IN_PROGRESS"]
        )
        maintenance_ranges = [(m.start_time, m.end_time) for m in maintenances]

        # 4. Check if user is staff/admin
        is_staff_or_admin = False
        if user and user.is_authenticated:
            is_staff_or_admin = (
                getattr(user, "role", "") in ("ADMIN", "STAFF")
                or user.is_superuser
            )

        # Pre-fetch booking references if needed
        booking_map = {}
        if is_staff_or_admin:
            from bookings.models import Booking
            day_bookings = Booking.objects.filter(
                turf=turf, date=date_obj
            ).select_related("customer")
            for b in day_bookings:
                booking_map[b.booking_id] = {
                    "id": str(b.id),
                    "booking_id": b.booking_id,
                    "customer_name": b.customer.full_name or b.customer.email,
                    "customer_phone": b.customer.phone or "",
                    "amount_paid": float(b.amount_paid),
                    "balance_due": float(b.balance_due),
                    "status": b.status,
                }

        from .serializers import TimeSlotSerializer

        if pricing_context is None:
            pricing_context = PricingEngine.get_pricing_context(turf, date_obj)

        # Compute timezone once for entire batch — avoids 4× per slot overhead
        now_local = timezone.localtime(timezone.now())
        now_context = (now_local.date(), now_local.time())

        serialized_slots = []
        slots_to_update = []

        for slot in slots:
            # Sync maintenance status
            is_in_maintenance = any(
                start <= slot.start_time and end >= slot.end_time
                for start, end in maintenance_ranges
            )
            if is_in_maintenance and slot.status != "MAINTENANCE":
                slot.status = "MAINTENANCE"
                slots_to_update.append(slot)
            elif not is_in_maintenance and slot.status == "MAINTENANCE":
                slot.status = "AVAILABLE"
                slots_to_update.append(slot)

            # Dynamic price calculation with preloaded pricing context (0 extra DB queries)
            price_info = PricingEngine.calculate_slot_price(
                turf, date_obj, slot.start_time, slot.end_time, pricing_context=pricing_context
            )

            slot_data = TimeSlotSerializer(slot, context={"now_context": now_context}).data
            slot_data["price"] = price_info["slot_price"]
            slot_data["base_price"] = price_info["base_price"]
            slot_data["applied_rules"] = price_info["applied_rules"]

            if is_staff_or_admin and slot.booking_id and slot.booking_id in booking_map:
                slot_data["booking_info"] = booking_map[slot.booking_id]

            serialized_slots.append(slot_data)

        if slots_to_update:
            TimeSlot.objects.bulk_update(slots_to_update, ["status"])

        available_count = sum(1 for s in serialized_slots if s.get("is_available"))
        is_fast_fill = available_count <= turf.fast_fill_threshold

        return {
            "turf_id": str(turf.id),
            "turf_name": turf.name,
            "date": str(date_obj),
            "base_price": float(turf.base_price),
            "available_slots_count": available_count,
            "is_fast_fill": is_fast_fill,
            "fast_fill_threshold": turf.fast_fill_threshold,
            "slots": serialized_slots,
        }
