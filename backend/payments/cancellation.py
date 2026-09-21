from datetime import datetime, time
from decimal import Decimal
from django.utils import timezone
from accounts.settings_helper import BusinessSettingsHelper


class CancellationPolicyEngine:
    """
    Automated Cancellation Policy Engine for Friends Turf.
    Calculates dynamic cancellation fees and refundable amounts based on match lead time
    and database-configured business rules.
    """

    @classmethod
    def calculate_refund(cls, booking, as_of_time=None):
        """
        Calculates refund eligibility and fee for a booking dynamically.
        """
        if as_of_time is None:
            as_of_time = timezone.now()

        amount_paid = Decimal(str(booking.amount_paid or 0))

        # Determine kickoff datetime
        kickoff_datetime = timezone.make_aware(
            datetime.combine(booking.date, booking.start_time)
        ) if timezone.is_naive(datetime.combine(booking.date, booking.start_time)) else datetime.combine(booking.date, booking.start_time)

        hours_until_kickoff = (kickoff_datetime - as_of_time).total_seconds() / 3600.0

        # Load dynamic rules
        rules = BusinessSettingsHelper.get_booking_rules()
        full_refund_hours = float(rules.get("cancellationFullRefundHours", 24))
        mid_tier_hours = float(rules.get("cancellationPartialRefundHours", 6))
        mid_tier_fee = Decimal(str(rules.get("cancellationFeeMidTierPercent", 20)))
        partial_refund_pct = Decimal(str(rules.get("partialRefundPercent", 50)))
        late_fee = Decimal("100.00") - partial_refund_pct  # e.g. 50%

        if hours_until_kickoff >= full_refund_hours:
            fee_percent = Decimal("0.00")
            policy_desc = f"Full Refund (> {int(full_refund_hours)}h before kickoff)"
            eligibility = "FULL"
        elif hours_until_kickoff >= mid_tier_hours:
            fee_percent = mid_tier_fee
            refund_pct = 100 - float(mid_tier_fee)
            policy_desc = f"{int(refund_pct)}% Refund ({int(mid_tier_hours)}h–{int(full_refund_hours)}h before kickoff; {int(mid_tier_fee)}% fee)"
            eligibility = "PARTIAL"
        elif hours_until_kickoff >= 2.0:
            fee_percent = late_fee
            refund_pct = 100 - float(late_fee)
            policy_desc = f"{int(refund_pct)}% Refund (2h–{int(mid_tier_hours)}h before kickoff; {int(late_fee)}% fee)"
            eligibility = "PARTIAL"
        else:
            fee_percent = Decimal("100.00")
            policy_desc = "Non-Refundable (< 2h before kickoff or match started)"
            eligibility = "NONE"

        cancellation_fee = round((amount_paid * fee_percent) / Decimal("100.00"), 2)
        refundable_amount = max(Decimal("0.00"), amount_paid - cancellation_fee)

        return {
            "booking_id": booking.booking_id,
            "amount_paid": float(amount_paid),
            "fee_percent": float(fee_percent),
            "cancellation_fee": float(cancellation_fee),
            "refundable_amount": float(refundable_amount),
            "hours_until_kickoff": round(hours_until_kickoff, 1),
            "policy_rule": policy_desc,
            "eligibility": eligibility,
        }
