from datetime import datetime, time
from decimal import Decimal
from django.utils import timezone
from .models import PricingRule, Holiday, SpecialEvent


class PricingEngine:
    TAX_RATE_PERCENTAGE = Decimal("18.00")  # Standard 18% GST

    @classmethod
    def calculate_slot_price(cls, turf, date_obj, start_time_obj, end_time_obj):
        """
        Calculates the dynamic price for a single slot based on:
        - Base Price of Turf
        - Weekend / Weekday rules
        - Peak hour / Off-peak rules
        - Holiday surge
        - Special event surge
        """
        base_price = Decimal(str(turf.base_price))
        current_price = base_price
        applied_rules = []

        day_of_week = date_obj.weekday()  # 0=Monday, 6=Sunday

        # 1. Check for Holiday surge
        holiday = Holiday.objects.filter(date=date_obj).first()
        if holiday:
            multiplier = Decimal(str(holiday.surge_multiplier))
            surge = round(base_price * (multiplier - Decimal("1.00")), 2)
            current_price += surge
            applied_rules.append(
                {
                    "name": f"Holiday Surge ({holiday.name})",
                    "type": "HOLIDAY",
                    "amount": float(surge),
                }
            )

        # 2. Check for Special Events
        special_event = SpecialEvent.objects.filter(date=date_obj).filter(
            turf__isnull=True
        ) | SpecialEvent.objects.filter(date=date_obj, turf=turf)
        event = special_event.first()
        if event:
            multiplier = Decimal(str(event.surge_multiplier))
            surge = round(base_price * (multiplier - Decimal("1.00")), 2)
            current_price += surge
            applied_rules.append(
                {
                    "name": f"Special Event ({event.name})",
                    "type": "SPECIAL_EVENT",
                    "amount": float(surge),
                }
            )

        # 3. Dynamic Pricing Rules
        rules = PricingRule.objects.filter(is_active=True).order_by("-priority")
        for rule in rules:
            # Check turf applicability
            if rule.turf and rule.turf_id != turf.id:
                continue

            # Check date range applicability
            if rule.start_date and date_obj < rule.start_date:
                continue
            if rule.end_date and date_obj > rule.end_date:
                continue

            # Check applicable days (e.g. 5,6 for weekend)
            if rule.applicable_days and day_of_week not in rule.applicable_days:
                continue

            # Check time range (e.g. peak hours between 18:00 and 23:00)
            if rule.start_time and rule.end_time:
                if not (
                    start_time_obj >= rule.start_time and start_time_obj < rule.end_time
                ):
                    continue

            # Rule applies! Calculate adjustment
            adj_val = Decimal(str(rule.adjustment_value))
            if rule.adjustment_type == "PERCENTAGE":
                adjustment = round((base_price * adj_val) / Decimal("100.00"), 2)
            else:
                adjustment = adj_val

            current_price += adjustment
            applied_rules.append(
                {"name": rule.name, "type": rule.rule_type, "amount": float(adjustment)}
            )

        # Avoid negative price
        final_slot_price = max(Decimal("100.00"), current_price)

        return {
            "base_price": float(base_price),
            "applied_rules": applied_rules,
            "slot_price": float(final_slot_price),
        }

    @classmethod
    def calculate_booking_total(
        cls, turf, date_obj, slot_items, coupon=None, user=None
    ):
        """
        Calculates the complete price breakdown for a booking:
        - Base amount for all slots + rule adjustments
        - Subtotal
        - Coupon discount
        - Membership discount
        - Tax amount (GST)
        - Final total
        """
        total_base = Decimal("0.00")
        total_adjustments = Decimal("0.00")
        slots_breakdown = []

        for item in slot_items:
            start_t = item["start_time"]
            end_t = item["end_time"]
            if isinstance(start_t, str):
                start_t = (
                    datetime.strptime(start_t, "%H:%M:%S").time()
                    if len(start_t) == 8
                    else datetime.strptime(start_t, "%H:%M").time()
                )
            if isinstance(end_t, str):
                end_t = (
                    datetime.strptime(end_t, "%H:%M:%S").time()
                    if len(end_t) == 8
                    else datetime.strptime(end_t, "%H:%M").time()
                )

            calc = cls.calculate_slot_price(turf, date_obj, start_t, end_t)
            total_base += Decimal(str(calc["base_price"]))
            slot_adj = sum(Decimal(str(r["amount"])) for r in calc["applied_rules"])
            total_adjustments += slot_adj
            slots_breakdown.append(
                {
                    "start_time": start_t.strftime("%H:%M"),
                    "end_time": end_t.strftime("%H:%M"),
                    "base_price": calc["base_price"],
                    "rules": calc["applied_rules"],
                    "final_slot_price": calc["slot_price"],
                }
            )

        subtotal = total_base + total_adjustments

        # Membership discount
        membership_discount = Decimal("0.00")
        if user and hasattr(user, "customer_profile"):
            tier = user.customer_profile.membership_tier.upper()
            if tier == "PLATINUM":
                membership_discount = round(
                    (subtotal * Decimal("15.00")) / Decimal("100.00"), 2
                )
            elif tier == "GOLD":
                membership_discount = round(
                    (subtotal * Decimal("10.00")) / Decimal("100.00"), 2
                )
            elif tier == "SILVER":
                membership_discount = round(
                    (subtotal * Decimal("5.00")) / Decimal("100.00"), 2
                )

        amount_after_membership = max(Decimal("0.00"), subtotal - membership_discount)

        # Coupon discount
        coupon_discount = Decimal("0.00")
        coupon_code = ""
        if coupon:
            coupon_code = coupon.code
            coupon_discount = Decimal(
                str(coupon.calculate_discount(amount_after_membership))
            )

        discount_amount = membership_discount + coupon_discount
        taxable_amount = max(Decimal("0.00"), subtotal - discount_amount)
        tax_amount = round(
            (taxable_amount * cls.TAX_RATE_PERCENTAGE) / Decimal("100.00"), 2
        )
        final_amount = taxable_amount + tax_amount

        return {
            "total_base": float(total_base),
            "total_adjustments": float(total_adjustments),
            "subtotal": float(subtotal),
            "membership_discount": float(membership_discount),
            "coupon_code": coupon_code,
            "coupon_discount": float(coupon_discount),
            "total_discount": float(discount_amount),
            "taxable_amount": float(taxable_amount),
            "tax_rate_percent": float(cls.TAX_RATE_PERCENTAGE),
            "tax_amount": float(tax_amount),
            "final_amount": float(final_amount),
            "slots_breakdown": slots_breakdown,
        }
