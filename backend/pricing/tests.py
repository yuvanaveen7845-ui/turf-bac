from datetime import date, time, timedelta
from decimal import Decimal
from django.test import TestCase
from django.utils import timezone
from accounts.models import User
from turfs.models import Turf, TimeSlot
from pricing.models import PricingRule, Holiday, SpecialEvent
from pricing.engine import PricingEngine


class PricingEngineTests(TestCase):
    def setUp(self):
        self.turf = Turf.objects.create(
            name="Super Turf Arena",
            slug="super-turf-arena",
            sport_type="FOOTBALL",
            description="FIFA quality turf",
            location="Tiruppur",
            address="Near RTO Backside",
            base_price=Decimal("1000.00"),  # Hourly base price ₹1000
            operating_hours_start=time(6, 0),
            operating_hours_end=time(23, 0),
            slot_duration_minutes=30,
        )
        self.target_date = date(2026, 10, 15)  # Thursday

        # Peak pricing rule: 18:00 to 23:00 (+20% surge)
        self.peak_rule = PricingRule.objects.create(
            name="Peak Evening Surge",
            rule_type="PEAK_HOUR",
            start_time=time(18, 0),
            end_time=time(23, 0),
            adjustment_type="PERCENTAGE",
            adjustment_value=Decimal("20.00"),
            priority=10,
            is_active=True,
        )

    def test_30_min_slot_base_price_prorated(self):
        """A 30-minute off-peak slot (10:30 to 11:00) should be base-priced at exactly 50% (₹500)."""
        pricing = PricingEngine.calculate_slot_price(
            turf=self.turf,
            date_obj=self.target_date,
            start_time_obj=time(10, 30),
            end_time_obj=time(11, 0),
        )
        self.assertEqual(pricing["base_price"], 500.0)
        self.assertEqual(pricing["slot_price"], 500.0)
        self.assertEqual(len(pricing["applied_rules"]), 0)

    def test_split_peak_pricing_boundary(self):
        """
        Booking 17:30 to 18:30 crosses peak threshold at 18:00:
        - 17:30 - 18:00 (30m off-peak): ₹500
        - 18:00 - 18:30 (30m peak +20%): ₹500 + ₹100 = ₹600
        Total booking final_amount must equal ₹1,100 (excluding tax adjustments).
        """
        slot_items = [
            {"start_time": time(17, 30), "end_time": time(18, 0)},
            {"start_time": time(18, 0), "end_time": time(18, 30)},
        ]
        total_data = PricingEngine.calculate_booking_total(
            turf=self.turf,
            date_obj=self.target_date,
            slot_items=slot_items,
        )

        slots_breakdown = total_data["slots_breakdown"]
        self.assertEqual(len(slots_breakdown), 2)

        # Slot 1: 17:30 - 18:00
        self.assertEqual(slots_breakdown[0]["final_slot_price"], 500.0)
        self.assertEqual(len(slots_breakdown[0]["rules"]), 0)

        # Slot 2: 18:00 - 18:30
        self.assertEqual(slots_breakdown[1]["final_slot_price"], 600.0)
        self.assertEqual(len(slots_breakdown[1]["rules"]), 1)
        self.assertEqual(slots_breakdown[1]["rules"][0]["name"], "Peak Evening Surge")
        self.assertEqual(slots_breakdown[1]["rules"][0]["amount"], 100.0)

        self.assertEqual(total_data["subtotal"], 1100.0)
        self.assertEqual(total_data["final_amount"], 1100.0)

