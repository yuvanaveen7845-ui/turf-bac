from datetime import datetime, timedelta, date, time
from decimal import Decimal
from django.db import transaction, models
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
    def cleanup_expired_locks(cls, turf=None, date_obj=None, force=False):
        """
        Releases any temporary slot locks that have passed their expiration timestamp.
        Throttled by default during read queries to prevent redundant database writes.
        """
        from django.core.cache import cache

        turf_key = str(getattr(turf, "id", turf) or "all")
        date_key = str(date_obj or "all")
        throttle_key = f"lock_cleanup_cooldown_{turf_key}_{date_key}"

        if not force and cache.get(throttle_key):
            return 0

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
        cache.set(throttle_key, 1, timeout=15)
        return count

    @classmethod
    def generate_daily_slots(cls, turf, date_obj):
        """
        Generates standard time slots for a turf on a given date in a single bulk operation based on:
        - turf.operating_hours_start (default 06:00)
        - turf.operating_hours_end (default 23:00)
        - turf.slot_duration_minutes (default 60 mins)
        Guarantees that no generated slot overlaps with ANY existing slot (booked, locked, or maintenance).
        """
        if getattr(turf, "is_deleted", False):
            return []

        start_limit_dt = datetime.combine(date_obj, turf.operating_hours_start)
        if turf.operating_hours_end <= turf.operating_hours_start:
            end_limit_dt = datetime.combine(date_obj + timedelta(days=1), turf.operating_hours_end)
        else:
            end_limit_dt = datetime.combine(date_obj, turf.operating_hours_end)

        duration_minutes = turf.slot_duration_minutes or 60

        # Existing slots on this day (booked, locked, maintenance, or already generated)
        existing_slots = list(
            TimeSlot.objects.filter(turf=turf, date=date_obj).values_list(
                "start_time", "end_time"
            )
        )
        existing_intervals = []
        for ex_start, ex_end in existing_slots:
            ex_s_dt = datetime.combine(date_obj, ex_start)
            ex_e_dt = datetime.combine(
                date_obj + timedelta(days=1 if ex_end <= ex_start else 0), ex_end
            )
            existing_intervals.append((ex_s_dt, ex_e_dt))

        new_slots = []
        cur_dt = start_limit_dt
        while True:
            slot_end_dt = cur_dt + timedelta(minutes=duration_minutes)
            if slot_end_dt > end_limit_dt:
                break

            cur_time = cur_dt.time()
            slot_end_time = slot_end_dt.time()

            # Check overlap with any existing slot: max(start1, start2) < min(end1, end2)
            has_overlap = False
            for ex_s_dt, ex_e_dt in existing_intervals:
                if max(cur_dt, ex_s_dt) < min(slot_end_dt, ex_e_dt):
                    has_overlap = True
                    break

            if not has_overlap:
                slot_price = (
                    round(
                        Decimal(str(turf.base_price))
                        * Decimal(str(duration_minutes))
                        / Decimal("60.0"),
                        2,
                    )
                    if duration_minutes != 60
                    else turf.base_price
                )
                new_slots.append(
                    TimeSlot(
                        turf=turf,
                        date=date_obj,
                        start_time=cur_time,
                        end_time=slot_end_time,
                        status="AVAILABLE",
                        price=slot_price,
                    )
                )

            cur_dt = slot_end_dt
            if cur_dt >= end_limit_dt:
                break

        if new_slots:
            TimeSlot.objects.bulk_create(new_slots, ignore_conflicts=True)

        return list(
            TimeSlot.objects.filter(turf=turf, date=date_obj).order_by("start_time")
        )

    @classmethod
    def realign_future_slots(cls, turf, days_ahead=14, target_date_only=None):
        """
        Re-aligns future time slots when operating hours, slot duration, or base price are updated.
        Preserves all confirmed customer bookings, active holds, scheduled maintenance, and admin blocks.
        Cleans all stale, mismatched-duration, or overlapping unbooked AVAILABLE slots.
        """
        if getattr(turf, "is_deleted", False):
            return

        now_local = timezone.localtime(timezone.now())
        today = now_local.date()

        dates_to_process = (
            [target_date_only]
            if target_date_only
            else [today + timedelta(days=offset) for offset in range(days_ahead + 1)]
        )

        for target_date in dates_to_process:
            with transaction.atomic():
                date_slots = TimeSlot.objects.filter(turf=turf, date=target_date)

                # Identify slots that MUST be preserved (Booked, Unexpired Holds, Maintenance, Admin Blocked)
                preserved_slots = list(
                    date_slots.filter(
                        models.Q(status="BOOKED")
                        | models.Q(status="LOCKED", locked_until__gte=timezone.now())
                        | models.Q(status="MAINTENANCE")
                        | models.Q(status="BLOCKED")
                    )
                )

                # Delete all unbooked, unlocked, and expired-hold slots to eliminate corruption and mismatched durations
                date_slots.exclude(id__in=[s.id for s in preserved_slots]).delete()

                # Re-generate fresh, uniform slots for this day around preserved slots
                cls.generate_daily_slots(turf, target_date)

    @staticmethod
    def _serialize_slot_fast(
        slot,
        turf,
        date_obj,
        pricing_context,
        now_date,
        now_time,
        now_utc,
        booking_info=None,
    ):
        """
        High-performance direct dict serialization for a time slot.
        Bypasses DRF ModelSerializer instantiation overhead (10-20x faster).
        """
        # Dynamic pricing calculation
        price_info = PricingEngine.calculate_slot_price(
            turf, date_obj, slot.start_time, slot.end_time, pricing_context=pricing_context
        )

        is_lock_expired = (
            slot.status == "LOCKED"
            and slot.locked_until
            and now_utc > slot.locked_until
        )

        # Time state evaluation
        is_past = (
            slot.date < now_date
            or (slot.date == now_date and slot.end_time <= now_time)
        )
        is_ongoing = (
            slot.date == now_date
            and slot.start_time <= now_time < slot.end_time
        )

        # Availability evaluation
        if slot.date < now_date or (slot.date == now_date and slot.start_time <= now_time):
            is_available = False
        elif slot.status == "AVAILABLE" or is_lock_expired:
            is_available = True
        else:
            is_available = False

        # Slot state evaluation
        if is_past:
            slot_state = "COMPLETED" if slot.status == "BOOKED" else "PAST"
        elif is_ongoing:
            slot_state = "ONGOING"
        elif slot.status in ("BOOKED", "MAINTENANCE", "BLOCKED"):
            slot_state = slot.status
        elif slot.status == "LOCKED" and not is_lock_expired:
            slot_state = "LOCKED"
        else:
            slot_state = "AVAILABLE"

        # 4-State Schedule indicator: AVAILABLE (Green), BOOKED (Red), BLOCKED (Slate), LOCKED (Amber)
        if slot.status in ("MAINTENANCE", "BLOCKED"):
            schedule_state = "BLOCKED"
        elif slot.status == "BOOKED":
            schedule_state = "BOOKED"
        elif slot.status == "LOCKED" and not is_lock_expired:
            schedule_state = "LOCKED"
        elif is_available:
            schedule_state = "AVAILABLE"
        else:
            schedule_state = slot.status

        slot_dict = {
            "id": str(slot.id),
            "turf": slot.turf_id,
            "date": str(slot.date),
            "start_time": slot.start_time.strftime("%H:%M:%S") if hasattr(slot.start_time, "strftime") else str(slot.start_time),
            "end_time": slot.end_time.strftime("%H:%M:%S") if hasattr(slot.end_time, "strftime") else str(slot.end_time),
            "status": slot.status,
            "schedule_state": schedule_state,
            "price": price_info["slot_price"],
            "base_price": price_info["base_price"],
            "applied_rules": price_info["applied_rules"],
            "is_available": is_available,
            "is_past": is_past,
            "is_ongoing": is_ongoing,
            "slot_state": slot_state,
            "locked_until": slot.locked_until.isoformat() if slot.locked_until else None,
            "booking_id": slot.booking_id or "",
        }

        if booking_info:
            slot_dict["booking_info"] = booking_info

        return slot_dict

    @classmethod
    def get_turf_availability(cls, turf, date_obj, user=None, pricing_context=None):
        """
        Computes the complete, authoritative availability of slots for a single turf on date_obj.
        Uses fast-path slot serialization.
        """
        cls.cleanup_expired_locks(turf=turf, date_obj=date_obj)

        slots = list(
            TimeSlot.objects.filter(turf=turf, date=date_obj).order_by("start_time")
        )

        # Self-healing integrity check: ensure slots match turf.slot_duration_minutes and do not overlap
        expected_duration = turf.slot_duration_minutes or 60
        needs_realign = False

        if not slots:
            needs_realign = True
        else:
            # 1. Check if any unbooked AVAILABLE slot has the wrong duration
            for s in slots:
                if s.status == "AVAILABLE":
                    s_dur = int(
                        (
                            datetime.combine(date_obj + timedelta(days=1 if s.end_time <= s.start_time else 0), s.end_time)
                            - datetime.combine(date_obj, s.start_time)
                        ).total_seconds()
                        / 60
                    )
                    if s_dur != expected_duration:
                        needs_realign = True
                        break
            # 2. Check if any adjacent slots overlap
            if not needs_realign:
                for i in range(len(slots) - 1):
                    s_curr = slots[i]
                    s_next = slots[i + 1]
                    curr_end_dt = datetime.combine(
                        date_obj + timedelta(days=1 if s_curr.end_time <= s_curr.start_time else 0),
                        s_curr.end_time,
                    )
                    next_start_dt = datetime.combine(date_obj, s_next.start_time)
                    if curr_end_dt > next_start_dt:
                        needs_realign = True
                        break

        if needs_realign:
            cls.realign_future_slots(turf, target_date_only=date_obj)
            slots = list(
                TimeSlot.objects.filter(turf=turf, date=date_obj).order_by("start_time")
            )

        maintenances = Maintenance.objects.filter(
            turf=turf, date=date_obj, status__in=["SCHEDULED", "IN_PROGRESS"]
        )
        maintenance_ranges = [(m.start_time, m.end_time) for m in maintenances]

        is_staff_or_admin = False
        if user and user.is_authenticated:
            is_staff_or_admin = (
                getattr(user, "role", "") in ("ADMIN", "STAFF")
                or user.is_superuser
            )

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

        if pricing_context is None:
            pricing_context = PricingEngine.get_pricing_context(turf, date_obj)

        now_utc = timezone.now()
        now_local = timezone.localtime(now_utc)
        now_date, now_time = now_local.date(), now_local.time()

        serialized_slots = []
        slots_to_update = []

        for slot in slots:
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

            b_info = booking_map.get(slot.booking_id) if is_staff_or_admin and slot.booking_id else None
            slot_data = cls._serialize_slot_fast(
                slot=slot,
                turf=turf,
                date_obj=date_obj,
                pricing_context=pricing_context,
                now_date=now_date,
                now_time=now_time,
                now_utc=now_utc,
                booking_info=b_info,
            )
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

    @classmethod
    def get_daily_schedule_batch(cls, turfs, date_obj, user=None, pricing_context=None):
        """
        High-Performance Single-Batch Schedule Engine:
        Processes all turfs for date_obj in O(1) total queries instead of O(N_turfs * 3).
        - 1 batch lock cleanup
        - 1 batch query for all active slots
        - 1 batch query for maintenance blackouts
        - 1 batch query for bookings (if staff/admin)
        - Vectorized fast dictionary serialization
        """
        from collections import defaultdict

        turfs_list = list(turfs)
        if not turfs_list:
            return []

        # 1. Single lock cleanup for date_obj
        cls.cleanup_expired_locks(date_obj=date_obj)

        if pricing_context is None:
            pricing_context = PricingEngine.get_pricing_context(date_obj=date_obj)

        # 2. Batch fetch maintenance blackouts for all requested turfs
        maintenances = Maintenance.objects.filter(
            turf__in=turfs_list, date=date_obj, status__in=["SCHEDULED", "IN_PROGRESS"]
        ).values_list("turf_id", "start_time", "end_time")

        maintenance_map = defaultdict(list)
        for turf_id, start_t, end_t in maintenances:
            maintenance_map[turf_id].append((start_t, end_t))

        # 3. Batch fetch bookings if staff/admin
        is_staff_or_admin = False
        if user and user.is_authenticated:
            is_staff_or_admin = (
                getattr(user, "role", "") in ("ADMIN", "STAFF")
                or user.is_superuser
            )

        booking_map = {}
        if is_staff_or_admin:
            from bookings.models import Booking
            day_bookings = Booking.objects.filter(
                turf__in=turfs_list, date=date_obj
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

        # 4. Batch fetch existing slots for all turfs
        all_slots = list(
            TimeSlot.objects.filter(turf__in=turfs_list, date=date_obj).order_by("start_time")
        )
        slots_by_turf = defaultdict(list)
        for slot in all_slots:
            slots_by_turf[slot.turf_id].append(slot)

        # Generate or self-heal slots for each turf on this date
        for turf in turfs_list:
            t_slots = slots_by_turf[turf.id]
            expected_dur = turf.slot_duration_minutes or 60
            needs_heal = False
            if not t_slots:
                needs_heal = True
            else:
                for s in t_slots:
                    if s.status == "AVAILABLE":
                        dur = int(
                            (
                                datetime.combine(date_obj + timedelta(days=1 if s.end_time <= s.start_time else 0), s.end_time)
                                - datetime.combine(date_obj, s.start_time)
                            ).total_seconds()
                            / 60
                        )
                        if dur != expected_dur:
                            needs_heal = True
                            break
                if not needs_heal:
                    for i in range(len(t_slots) - 1):
                        s_curr = t_slots[i]
                        s_next = t_slots[i + 1]
                        curr_end_dt = datetime.combine(
                            date_obj + timedelta(days=1 if s_curr.end_time <= s_curr.start_time else 0),
                            s_curr.end_time,
                        )
                        next_start_dt = datetime.combine(date_obj, s_next.start_time)
                        if curr_end_dt > next_start_dt:
                            needs_heal = True
                            break
            if needs_heal:
                cls.realign_future_slots(turf, target_date_only=date_obj)
                slots_by_turf[turf.id] = list(
                    TimeSlot.objects.filter(turf=turf, date=date_obj).order_by("start_time")
                )

        now_utc = timezone.now()
        now_local = timezone.localtime(now_utc)
        now_date, now_time = now_local.date(), now_local.time()

        turfs_data = []
        slots_to_update = []

        for turf in turfs_list:
            turf_slots = slots_by_turf[turf.id]
            m_ranges = maintenance_map.get(turf.id, [])

            serialized_slots = []
            for slot in turf_slots:
                # Sync maintenance status
                is_in_maintenance = any(
                    start <= slot.start_time and end >= slot.end_time
                    for start, end in m_ranges
                )
                if is_in_maintenance and slot.status != "MAINTENANCE":
                    slot.status = "MAINTENANCE"
                    slots_to_update.append(slot)
                elif not is_in_maintenance and slot.status == "MAINTENANCE":
                    slot.status = "AVAILABLE"
                    slots_to_update.append(slot)

                b_info = booking_map.get(slot.booking_id) if is_staff_or_admin and slot.booking_id else None
                slot_data = cls._serialize_slot_fast(
                    slot=slot,
                    turf=turf,
                    date_obj=date_obj,
                    pricing_context=pricing_context,
                    now_date=now_date,
                    now_time=now_time,
                    now_utc=now_utc,
                    booking_info=b_info,
                )
                serialized_slots.append(slot_data)

            available_count = sum(1 for s in serialized_slots if s.get("is_available"))
            is_fast_fill = available_count <= turf.fast_fill_threshold

            turfs_data.append(
                {
                    "id": str(turf.id),
                    "name": turf.name,
                    "slug": turf.slug,
                    "sport_type": turf.sport_type,
                    "base_price": float(turf.base_price),
                    "capacity": turf.capacity,
                    "dimensions": turf.dimensions,
                    "surface_spec": turf.surface_spec,
                    "lighting_spec": turf.lighting_spec,
                    "is_fifa_certified": turf.is_fifa_certified,
                    "images": turf.images,
                    "available_slots_count": available_count,
                    "is_fast_fill": is_fast_fill,
                    "slots": serialized_slots,
                }
            )

        if slots_to_update:
            TimeSlot.objects.bulk_update(slots_to_update, ["status"])

        return turfs_data
