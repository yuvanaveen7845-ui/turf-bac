from datetime import datetime, timedelta, time
from decimal import Decimal
from rest_framework import generics, permissions, status
from rest_framework.response import Response
from django.shortcuts import get_object_or_404
from .models import PricingRule, Holiday, SpecialEvent
from .serializers import (
    PricingRuleSerializer,
    HolidaySerializer,
    SpecialEventSerializer,
)
from .engine import PricingEngine
from accounts.permissions import IsAdmin, IsStaffOrAdmin
from turfs.models import Turf


class PricingRuleListCreateView(generics.ListCreateAPIView):
    queryset = PricingRule.objects.all().order_by("-priority")
    serializer_class = PricingRuleSerializer

    def get_permissions(self):
        if self.request.method == "GET":
            return [permissions.AllowAny()]
        return [IsAdmin()]


class PricingRuleDetailView(generics.RetrieveUpdateDestroyAPIView):
    queryset = PricingRule.objects.all()
    serializer_class = PricingRuleSerializer
    permission_classes = [IsAdmin]


class HolidayListCreateView(generics.ListCreateAPIView):
    queryset = Holiday.objects.all().order_by("date")
    serializer_class = HolidaySerializer

    def get_permissions(self):
        if self.request.method == "GET":
            return [permissions.AllowAny()]
        return [IsAdmin()]


class SpecialEventListCreateView(generics.ListCreateAPIView):
    queryset = SpecialEvent.objects.all().order_by("date")
    serializer_class = SpecialEventSerializer

    def get_permissions(self):
        if self.request.method == "GET":
            return [permissions.AllowAny()]
        return [IsAdmin()]


class PricingConflictDetectView(generics.GenericAPIView):
    """
    Analyzes active pricing rules to identify overlapping rules on the same turf, day, and time slots.
    """
    permission_classes = [IsStaffOrAdmin]

    def get(self, request):
        rules = list(PricingRule.objects.filter(is_active=True).order_by("-priority"))
        conflicts = []
        day_names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]

        for i, r1 in enumerate(rules):
            for r2 in rules[i + 1:]:
                # Check if same turf (or both apply globally)
                turf_overlap = (r1.turf == r2.turf) or (r1.turf is None) or (r2.turf is None)
                if not turf_overlap:
                    continue

                # Check if overlapping days
                days_overlap = False
                common_days = []
                if not r1.applicable_days or not r2.applicable_days:
                    days_overlap = True
                    common_days = ["All Days"]
                else:
                    shared_indices = list(set(r1.applicable_days) & set(r2.applicable_days))
                    if shared_indices:
                        days_overlap = True
                        common_days = [day_names[d] for d in shared_indices if d < len(day_names)]

                if not days_overlap:
                    continue

                # Check if overlapping hours
                time_overlap = True
                if r1.start_time and r1.end_time and r2.start_time and r2.end_time:
                    if r1.end_time <= r2.start_time or r2.end_time <= r1.start_time:
                        time_overlap = False

                if time_overlap:
                    higher = r1 if r1.priority >= r2.priority else r2
                    lower = r2 if r1.priority >= r2.priority else r1
                    conflicts.append({
                        "rule_1": {"id": r1.id, "name": r1.name, "priority": r1.priority, "adjustment": f"{r1.adjustment_value}"},
                        "rule_2": {"id": r2.id, "name": r2.name, "priority": r2.priority, "adjustment": f"{r2.adjustment_value}"},
                        "rule_a": {"id": r1.id, "name": r1.name, "priority": r1.priority, "adjustment": f"{r1.adjustment_value}"},
                        "rule_b": {"id": r2.id, "name": r2.name, "priority": r2.priority, "adjustment": f"{r2.adjustment_value}"},
                        "overlapping_days": common_days or ["Overlapping weekdays"],
                        "resolution": f"Rule '{higher.name}' (Priority {higher.priority}) will override '{lower.name}' (Priority {lower.priority}).",
                        "severity": "WARNING" if higher.priority == lower.priority else "INFO",
                    })

        return Response({
            "total_rules": len(rules),
            "conflicts_count": len(conflicts),
            "conflicts": conflicts,
        })


class PricingSimulateView(generics.GenericAPIView):
    """
    Interactive price preview calculator for Admin:
    Simulates base price, matching rules, surge multipliers, discounts, and final rate.
    """
    permission_classes = [IsStaffOrAdmin]

    def post(self, request):
        turf_id = request.data.get("turf_id")
        date_str = request.data.get("date")
        start_time_str = request.data.get("start_time", "18:00")
        duration_minutes = int(request.data.get("duration_minutes", 60))
        end_time_str = request.data.get("end_time")

        if not turf_id or not date_str:
            return Response({"error": "turf_id and date are required."}, status=status.HTTP_400_BAD_REQUEST)

        turf = get_object_or_404(Turf, pk=turf_id)
        try:
            date_obj = datetime.strptime(date_str, "%Y-%m-%d").date()
            if len(start_time_str) == 5:
                start_dt = datetime.strptime(f"{date_str} {start_time_str}", "%Y-%m-%d %H:%M")
            else:
                start_dt = datetime.strptime(f"{date_str} {start_time_str[:5]}", "%Y-%m-%d %H:%M")

            if end_time_str:
                if len(end_time_str) == 5:
                    end_dt = datetime.strptime(f"{date_str} {end_time_str}", "%Y-%m-%d %H:%M")
                else:
                    end_dt = datetime.strptime(f"{date_str} {end_time_str[:5]}", "%Y-%m-%d %H:%M")
            else:
                end_dt = start_dt + timedelta(minutes=duration_minutes)

            start_t = start_dt.time()
            end_t = end_dt.time()
        except ValueError as err:
            return Response({"error": f"Invalid date or time format: {err}"}, status=status.HTTP_400_BAD_REQUEST)

        slot_items = [{"start_time": start_t, "end_time": end_t}]
        price_result = PricingEngine.calculate_booking_total(
            turf=turf,
            date_obj=date_obj,
            slot_items=slot_items,
            coupon=None,
            user=request.user,
        )

        # Retrieve all matching rules for transparency
        matching_rules = []
        day_of_week = date_obj.weekday()
        for rule in PricingRule.objects.filter(is_active=True).order_by("-priority"):
            if rule.turf and rule.turf != turf:
                continue
            if rule.applicable_days and day_of_week not in rule.applicable_days:
                continue
            if rule.start_date and rule.start_date > date_obj:
                continue
            if rule.end_date and rule.end_date < date_obj:
                continue
            matching_rules.append({
                "name": rule.name,
                "type": rule.rule_type,
                "priority": rule.priority,
                "adjustment_type": rule.adjustment_type,
                "adjustment_value": float(rule.adjustment_value),
            })

        final_price = float(price_result.get("final_amount", turf.base_price))
        base_hourly = float(turf.base_price)
        adjustment_diff = final_price - base_hourly

        day_names = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]

        return Response({
            "turf_name": turf.name,
            "day_of_week": day_names[day_of_week],
            "date": date_str,
            "start_time": start_t.strftime("%H:%M"),
            "end_time": end_t.strftime("%H:%M"),
            "duration_minutes": duration_minutes,
            "base_hourly_price": base_hourly,
            "final_price": final_price,
            "adjustment_applied": adjustment_diff,
            "rules_matched": matching_rules,
            "calculation": price_result,
            "matching_rules": matching_rules,
        })
