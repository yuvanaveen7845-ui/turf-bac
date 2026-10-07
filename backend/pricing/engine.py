from datetime import datetime, time
from decimal import Decimal
from django.db.models import Q
from django.utils import timezone
from .models import PricingRule, Holiday, SpecialEvent
from accounts.settings_helper import BusinessSettingsHelper


class PricingEngine:
    @classmethod
    def get_tax_rate_percentage(cls) -> Decimal:
        return BusinessSettingsHelper.get_tax_rate_percentage()

    # Backward compatibility property/attribute alias
    TAX_RATE_PERCENTAGE = Decimal("18.00")

    @classmethod
    def invalidate_pricing_cache(cls, turf=None):
        """
        Atomically busts all cached pricing contexts across all dates and turfs,
        and broadcasts a PRICE_CHANGED real-time event to connected clients.
        """
        import time
        from django.core.cache import cache
        from realtime.events import publish_event

        # Increment version to instantly bust all pricing_ctx_* keys
        try:
            cache.incr("pricing_cache_version")
        except Exception:
            cache.set("pricing_cache_version", int(time.time()), timeout=None)

        # Invalidate public turfs cache as well
        for sport in ["ALL", "FOOTBALL", "CRICKET", "MULTI_SPORT", "BADMINTON", "TENNIS"]:
            cache.delete(f"turfs_public_list_{sport}")

        # Broadcast live price update to customer and staff screens
        try:
            publish_event(
                channel="slots",
                event_type="PRICE_CHANGED",
                payload={
                    "turf_id": str(turf.id) if turf else "all",
                    "timestamp": time.time(),
                },
            )
        except Exception:
            pass

    @classmethod
    def get_pricing_context(cls, turf=None, date_obj=None):
        """
        Pre-fetches all pricing rules, holidays, and special events in a single batch.
        Eliminates repeated database roundtrips when calculating slot pricing matrices.
        Caches in LocMemCache with version-based instant invalidation.
        """
        from django.core.cache import cache

        turf_key = str(turf.id) if turf else "all"
        date_key = str(date_obj) if date_obj else "nodate"
        cache_ver = cache.get("pricing_cache_version", 1)
        cache_key = f"pricing_ctx_v{cache_ver}_{turf_key}_{date_key}"

        cached = cache.get(cache_key)
        if cached is not None:
            return cached

        rules = list(PricingRule.objects.filter(is_active=True).order_by("-priority"))
        holiday = None
        special_event = None
        if date_obj:
            holiday = Holiday.objects.filter(date=date_obj).first()
            se_qs = SpecialEvent.objects.filter(date=date_obj)
            if turf:
                special_event = se_qs.filter(Q(turf__isnull=True) | Q(turf=turf)).first()
            else:
                special_event = se_qs.first()

        context_data = {
            "rules": rules,
            "holiday": holiday,
            "special_event": special_event,
        }
        cache.set(cache_key, context_data, timeout=300)
        return context_data

    @classmethod
    def calculate_slot_price(cls, turf, date_obj, start_time_obj, end_time_obj, pricing_context=None):
        """
        Calculates the dynamic price for a single slot based on:
        - Base Price of Turf
        - Weekend / Weekday rules
        - Peak hour / Off-peak rules
        - Holiday surge
        - Special event surge
        Supports optional pre-fetched pricing_context to avoid N+1 DB queries.
        """
        base_price = Decimal(str(turf.base_price))

        # Defensively normalize start_time_obj and end_time_obj to datetime.time
        if isinstance(start_time_obj, str):
            try:
                start_time_obj = (
                    datetime.strptime(start_time_obj, "%H:%M:%S").time()
                    if len(start_time_obj) == 8
                    else datetime.strptime(start_time_obj[:5], "%H:%M").time()
                )
            except Exception:
                pass
        if isinstance(end_time_obj, str):
            try:
                end_time_obj = (
                    datetime.strptime(end_time_obj, "%H:%M:%S").time()
                    if len(end_time_obj) == 8
                    else datetime.strptime(end_time_obj[:5], "%H:%M").time()
                )
            except Exception:
                pass

        # Prorate base price for slot duration (e.g. 30m slot = base_price * 30 / 60)
        if start_time_obj and end_time_obj:
            try:
                sh, sm = (
                    (start_time_obj.hour, start_time_obj.minute)
                    if hasattr(start_time_obj, "hour")
                    else [int(x) for x in str(start_time_obj).split(":")[:2]]
                )
                eh, em = (
                    (end_time_obj.hour, end_time_obj.minute)
                    if hasattr(end_time_obj, "hour")
                    else [int(x) for x in str(end_time_obj).split(":")[:2]]
                )
                duration_mins = (eh * 60 + em) - (sh * 60 + sm)
                if duration_mins < 0:
                    duration_mins += 24 * 60
                if duration_mins > 0 and duration_mins != 60:
                    base_price = round(base_price * Decimal(str(duration_mins)) / Decimal("60.0"), 2)
            except Exception:
                pass

        # Check feature flag for dynamic surge pricing
        if not BusinessSettingsHelper.is_feature_enabled("DYNAMIC_PRICING", default=True):
            min_slot_price = Decimal(str(BusinessSettingsHelper.get_payment_settings().get("minSlotPrice", 1.0)))
            return {
                "base_price": float(base_price),
                "applied_rules": [],
                "slot_price": float(max(min_slot_price, base_price)),
            }

        current_price = base_price
        applied_rules = []

        day_of_week = date_obj.weekday()  # 0=Monday, 6=Sunday

        if pricing_context is not None:
            holiday = pricing_context.get("holiday")
            event = pricing_context.get("special_event")
            rules = pricing_context.get("rules", [])
        else:
            holiday = Holiday.objects.filter(date=date_obj).first()
            special_event = SpecialEvent.objects.filter(date=date_obj).filter(
                turf__isnull=True
            ) | SpecialEvent.objects.filter(date=date_obj, turf=turf)
            event = special_event.first()
            rules = list(PricingRule.objects.filter(is_active=True).order_by("-priority"))

        # 1. Check for Holiday surge
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

        # 3. Dynamic Pricing Rules (Ordered by -priority)
        applied_rule_types = {}  # Map rule_type -> highest priority applied
        has_fixed_slot_override = False

        for rule in rules:
            # Check turf applicability
            if rule.turf_id and rule.turf_id != turf.id:
                continue

            # Check date range applicability
            if rule.start_date and date_obj < rule.start_date:
                continue
            if rule.end_date and date_obj > rule.end_date:
                continue

            # Check applicable days (e.g. 5,6 for weekend)
            if rule.applicable_days and day_of_week not in rule.applicable_days:
                continue

            # Check time range (supports midnight crossing e.g. 22:00 to 02:00, or 22:00 to 00:00)
            if rule.start_time and rule.end_time and rule.start_time != rule.end_time:
                if rule.start_time < rule.end_time:
                    if not (start_time_obj >= rule.start_time and start_time_obj < rule.end_time):
                        continue
                else:
                    # Midnight crossing
                    if rule.end_time == time(0, 0):
                        if not (start_time_obj >= rule.start_time):
                            continue
                    else:
                        if not (start_time_obj >= rule.start_time or start_time_obj < rule.end_time):
                            continue
            elif rule.start_time and not rule.end_time:
                if start_time_obj < rule.start_time:
                    continue
            elif rule.end_time and not rule.start_time:
                if start_time_obj >= rule.end_time:
                    continue

            # Conflict resolution:
            # 1. If an explicit fixed slot override (priority >= 20) was already applied, skip general rules
            if has_fixed_slot_override and rule.priority < 20:
                continue

            # 2. A higher-priority rule of the SAME rule_type overrides lower-priority rules of that type
            if rule.rule_type in applied_rule_types:
                continue

            # Rule applies! Calculate adjustment
            adj_val = Decimal(str(rule.adjustment_value))
            if rule.adjustment_type == "PERCENTAGE":
                adjustment = round((base_price * adj_val) / Decimal("100.00"), 2)
            else:
                adjustment = adj_val

            current_price += adjustment
            applied_rule_types[rule.rule_type] = rule.priority
            if rule.priority >= 20 and rule.adjustment_type == "FIXED":
                has_fixed_slot_override = True

            applied_rules.append(
                {"name": rule.name, "type": rule.rule_type, "amount": float(adjustment)}
            )

        # Dynamic floor price from settings
        min_slot_price = Decimal(str(BusinessSettingsHelper.get_payment_settings().get("minSlotPrice", 1.0)))
        final_slot_price = max(min_slot_price, current_price)

        return {
            "base_price": float(base_price),
            "applied_rules": applied_rules,
            "slot_price": float(final_slot_price),
        }

    @classmethod
    def calculate_booking_total(
        cls,
        turf,
        date_obj,
        slot_items,
        coupon=None,
        user=None,
        manual_override_price=None,
        manual_discount=None,
        override_reason="",
        actor=None,
    ):
        """
        Calculates the complete price breakdown for a booking:
        - Base amount for all slots + rule adjustments
        - Subtotal
        - Coupon discount
        - Membership discount (dynamic from active MembershipPlan)
        - Tax amount (GST dynamically from BusinessSetting)
        - Final total
        - Explicit discounts & manual price overrides (with role authorization)
        """
        total_base = Decimal("0.00")
        subtotal = Decimal("0.00")
        slots_breakdown = []
        pricing_context = cls.get_pricing_context(turf, date_obj)

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

            calc = cls.calculate_slot_price(turf, date_obj, start_t, end_t, pricing_context=pricing_context)
            slot_base = Decimal(str(calc["base_price"]))
            slot_price = Decimal(str(calc["slot_price"]))
            total_base += slot_base
            subtotal += slot_price
            slots_breakdown.append(
                {
                    "start_time": start_t.strftime("%H:%M"),
                    "end_time": end_t.strftime("%H:%M"),
                    "base_price": calc["base_price"],
                    "rules": calc["applied_rules"],
                    "final_slot_price": calc["slot_price"],
                }
            )

        total_adjustments = subtotal - total_base
        discount_sources = []

        # Dynamic Membership discount from active subscription or MembershipPlan
        membership_discount = Decimal("0.00")
        membership_label = ""
        if user and user.is_authenticated:
            # Check active subscription
            from memberships.models import CustomerMembership, MembershipPlan
            active_membership = CustomerMembership.objects.filter(
                customer=user, status="ACTIVE", end_date__gte=timezone.now().date()
            ).select_related("plan").first()

            if active_membership and active_membership.plan:
                plan = active_membership.plan
                pct = Decimal(str(plan.discount_percentage))
                if pct > 0:
                    membership_discount = round((subtotal * pct) / Decimal("100.00"), 2)
                    membership_label = f"{plan.name} Member ({pct}% Off)"
            elif hasattr(user, "customer_profile"):
                tier = user.customer_profile.membership_tier.strip().upper()
                plan = MembershipPlan.objects.filter(slug__iexact=tier, is_active=True).first()
                if plan and plan.discount_percentage > 0:
                    pct = Decimal(str(plan.discount_percentage))
                    membership_discount = round((subtotal * pct) / Decimal("100.00"), 2)
                    membership_label = f"{plan.name} Tier ({pct}% Off)"
                elif tier == "PLATINUM":
                    membership_discount = round((subtotal * Decimal("15.00")) / Decimal("100.00"), 2)
                    membership_label = "Platinum Tier (15% Off)"
                elif tier == "GOLD":
                    membership_discount = round((subtotal * Decimal("10.00")) / Decimal("100.00"), 2)
                    membership_label = "Gold Tier (10% Off)"
                elif tier == "SILVER":
                    membership_discount = round((subtotal * Decimal("5.00")) / Decimal("100.00"), 2)
                    membership_label = "Silver Tier (5% Off)"

        if membership_discount > Decimal("0.00"):
            discount_sources.append({
                "source": "MEMBERSHIP",
                "label": membership_label or "Membership Discount",
                "amount": float(membership_discount),
            })

        # Manual discount with actor permission check
        admin_discount = Decimal("0.00")
        if manual_discount:
            discount_val = Decimal(str(manual_discount))
            if discount_val > Decimal("0.00"):
                if actor:
                    if getattr(actor, "role", "") == "STAFF":
                        raise ValueError("Staff are not authorized to apply manual discounts.")
                admin_discount = discount_val
                discount_sources.append({
                    "source": "ADMIN_OVERRIDE",
                    "label": "Admin Approved Discount",
                    "amount": float(admin_discount),
                    "reason": override_reason or "Customer accommodation",
                    "actor": actor.email if actor else "Admin",
                })

        coupon_discount = Decimal("0.00")
        coupon_code = ""
        total_discount = membership_discount + admin_discount
        discounted_total = max(Decimal("0.00"), subtotal - total_discount)

        # Dynamic tax rate percentage from BusinessSetting
        tax_rate = cls.get_tax_rate_percentage()
        is_tax_included = BusinessSettingsHelper.is_tax_included()

        if is_tax_included:
            # Slot prices are GST-inclusive (no additional GST added on top).
            # If a slot is priced at ₹1000, final payable amount is ₹1000.
            # Included statutory GST = total * (tax_rate / (100 + tax_rate))
            calculated_final = discounted_total
            if tax_rate > Decimal("0.00") and calculated_final > Decimal("0.00"):
                tax_amount = round(
                    (calculated_final * tax_rate) / (Decimal("100.00") + tax_rate), 2
                )
                taxable_amount = calculated_final - tax_amount
            else:
                tax_amount = Decimal("0.00")
                taxable_amount = calculated_final
        else:
            taxable_amount = discounted_total
            tax_amount = round(
                (taxable_amount * tax_rate) / Decimal("100.00"), 2
            )
            calculated_final = taxable_amount + tax_amount

        # Manual Price Override
        is_overridden = False
        original_standard_total = float(calculated_final)
        final_amount = calculated_final

        if manual_override_price is not None:
            override_val = Decimal(str(manual_override_price))
            if actor:
                if getattr(actor, "role", "") == "STAFF":
                    raise ValueError("Staff are not authorized to override booking pricing.")
            final_amount = max(Decimal("0.00"), override_val)
            is_overridden = True
            if is_tax_included and tax_rate > Decimal("0.00") and final_amount > Decimal("0.00"):
                tax_amount = round(
                    (final_amount * tax_rate) / (Decimal("100.00") + tax_rate), 2
                )
                taxable_amount = final_amount - tax_amount

        return {
            "total_base": float(total_base),
            "total_adjustments": float(total_adjustments),
            "subtotal": float(subtotal),
            "membership_discount": float(membership_discount),
            "coupon_code": coupon_code,
            "coupon_discount": float(coupon_discount),
            "admin_discount": float(admin_discount),
            "total_discount": float(total_discount),
            "discount_sources": discount_sources,
            "taxable_amount": float(taxable_amount),
            "tax_rate_percent": float(tax_rate),
            "tax_amount": float(tax_amount),
            "is_tax_included": is_tax_included,
            "final_amount": float(final_amount),
            "original_standard_total": original_standard_total,
            "is_price_overridden": is_overridden,
            "override_reason": override_reason if is_overridden else "",
            "override_actor": actor.email if (is_overridden and actor) else "",
            "slots_breakdown": slots_breakdown,
        }
