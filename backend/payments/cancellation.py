from datetime import datetime, time
from decimal import Decimal
from django.utils import timezone


class CancellationPolicyEngine:
    """
    Automated Cancellation Policy Engine for Friends Turf.
    Calculates cancellation fees and refundable amounts based on match lead time:
    - > 24 Hours: 100% Refundable (₹0 cancellation fee)
    - 6 to 24 Hours: 80% Refundable (20% cancellation fee)
    - 2 to 6 Hours: 50% Refundable (50% cancellation fee)
    - < 2 Hours or After Kickoff: 0% Refundable (100% cancellation fee)
    """

    @classmethod
    def calculate_refund(cls, booking, as_of_time=None):
        """
        Calculates refund eligibility and fee for a booking.
        """
        if as_of_time is None:
            as_of_time = timezone.now()

        amount_paid = Decimal(str(booking.amount_paid or 0))

        # Determine kickoff datetime
        kickoff_datetime = timezone.make_aware(
            datetime.combine(booking.date, booking.start_time)
        ) if timezone.is_naive(datetime.combine(booking.date, booking.start_time)) else datetime.combine(booking.date, booking.start_time)

        hours_until_kickoff = (kickoff_datetime - as_of_time).total_seconds() / 3600.0

        if hours_until_kickoff >= 24.0:
            fee_percent = Decimal("0.00")
            policy_desc = "Full Refund (> 24h before kickoff)"
            eligibility = "FULL"
        elif hours_until_kickoff >= 6.0:
            fee_percent = Decimal("20.00")
            policy_desc = "80% Refund (6h–24h before kickoff; 20% cancellation fee)"
            eligibility = "PARTIAL"
        elif hours_until_kickoff >= 2.0:
            fee_percent = Decimal("50.00")
            policy_desc = "50% Refund (2h–6h before kickoff; 50% cancellation fee)"
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
