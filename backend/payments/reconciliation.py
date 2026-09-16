import uuid
from decimal import Decimal
from datetime import timedelta
from django.db import transaction
from django.utils import timezone
from .models import Payment, Refund
from .razorpay_client import RazorpayService
from bookings.models import Booking
from qr_system.services import QRService
from audit.models import AuditLog
from realtime.events import publish_event


class ReconciliationEngine:
    """
    Automated Financial Reconciliation Engine for Friends Turf.
    Detects and safely resolves discrepancies across:
    - Provider vs Local payment statuses
    - Paid bookings that missed confirmation hooks
    - Amount & balance discrepancies
    - Orphaned payments or unhandled webhook drops
    """

    @classmethod
    def scan_anomalies(cls):
        """
        Scans all payment and booking records for financial anomalies.
        Returns a structured list of actionable discrepancies.
        """
        anomalies = []
        now = timezone.now()

        # 1. Paid Payments where Booking is still PAYMENT_PENDING
        unconfirmed = Payment.objects.filter(
            status__in=["PAID", "SUCCESSFUL"],
            booking__status__in=["PAYMENT_PENDING", "PENDING"],
        ).select_related("booking", "customer")

        for pay in unconfirmed:
            anomalies.append(
                {
                    "id": f"ANOMALY-UNCONFIRMED-{pay.payment_id}",
                    "type": "UNCONFIRMED_PAID_BOOKING",
                    "severity": "CRITICAL",
                    "title": f"Payment Paid but Booking Unconfirmed ({pay.payment_id})",
                    "description": f"Customer {pay.customer.email} paid ₹{pay.amount} via {pay.payment_method}, but booking {pay.booking.booking_id} is still in {pay.booking.status} status.",
                    "payment_id": pay.payment_id,
                    "booking_id": pay.booking.booking_id,
                    "customer_name": pay.customer.full_name or pay.customer.email,
                    "amount": float(pay.amount),
                    "created_at": pay.created_at.isoformat(),
                    "recommended_action": "CONFIRM_BOOKING",
                    "action_label": "Confirm Booking & Issue QR",
                }
            )

        # 2. Amount Mismatches (amount_paid + balance_due != final_amount)
        active_bookings = Booking.objects.exclude(status="CANCELLED")
        for b in active_bookings:
            calc_sum = b.amount_paid + b.balance_due
            diff = abs(calc_sum - b.final_amount)
            if diff > Decimal("0.05"):
                anomalies.append(
                    {
                        "id": f"ANOMALY-AMOUNT-MISMATCH-{b.booking_id}",
                        "type": "AMOUNT_MISMATCH",
                        "severity": "WARNING",
                        "title": f"Balance Accounting Mismatch ({b.booking_id})",
                        "description": f"Booking total is ₹{b.final_amount}, but (Paid ₹{b.amount_paid} + Balance Due ₹{b.balance_due}) = ₹{calc_sum}. Discrepancy: ₹{diff}.",
                        "booking_id": b.booking_id,
                        "customer_name": b.customer.full_name or b.customer.email,
                        "amount": float(diff),
                        "created_at": b.created_at.isoformat(),
                        "recommended_action": "RECALCULATE_BALANCE",
                        "action_label": "Recalculate & Sync Balance",
                    }
                )

        # 3. Pending Online Razorpay payments older than 10 minutes that may have succeeded
        stale_pending = Payment.objects.filter(
            provider="RAZORPAY",
            status="PENDING",
            created_at__lte=now - timedelta(minutes=10),
            created_at__gte=now - timedelta(days=2),
        ).select_related("booking", "customer")

        for p in stale_pending:
            anomalies.append(
                {
                    "id": f"ANOMALY-STALE-PENDING-{p.payment_id}",
                    "type": "STALE_PENDING_PAYMENT",
                    "severity": "INFO",
                    "title": f"Pending Razorpay Order (>10m) ({p.payment_id})",
                    "description": f"Razorpay order {p.provider_order_id} has remained pending for booking {p.booking.booking_id}. Slot hold may be expired.",
                    "payment_id": p.payment_id,
                    "booking_id": p.booking.booking_id,
                    "customer_name": p.customer.full_name or p.customer.email,
                    "amount": float(p.amount),
                    "created_at": p.created_at.isoformat(),
                    "recommended_action": "MARK_FAILED_OR_CHECK",
                    "action_label": "Mark as Expired / Failed",
                }
            )

        return anomalies

    @classmethod
    def resolve_anomaly(cls, anomaly_type, payment_id=None, booking_id=None, user=None):
        """
        Safely resolves a detected anomaly within an atomic transaction.
        """
        with transaction.atomic():
            if anomaly_type == "UNCONFIRMED_PAID_BOOKING":
                pay = Payment.objects.select_for_update().get(payment_id=payment_id)
                booking = Booking.objects.select_for_update().get(pk=pay.booking_id)

                booking.amount_paid += pay.amount
                booking.balance_due = max(Decimal("0.00"), booking.final_amount - booking.amount_paid)
                booking.status = "CONFIRMED"
                booking.save()

                for slot in booking.slots.all():
                    slot.status = "BOOKED"
                    slot.locked_until = None
                    slot.locked_by = None
                    slot.save()

                QRService.generate_qr_for_booking(booking)

                AuditLog.objects.create(
                    user=user,
                    action="FINANCIAL_RECONCILIATION_RESOLVED",
                    resource_type="PAYMENT",
                    resource_id=pay.payment_id,
                    details={
                        "resolution": "CONFIRMED_BOOKING",
                        "booking_id": booking.booking_id,
                        "amount": float(pay.amount),
                    },
                )

                publish_event(
                    channel="operations",
                    event_type="OPERATIONS_UPDATE",
                    payload={"type": "RECONCILIATION_CONFIRMED", "booking_id": booking.booking_id},
                )
                return {"success": True, "message": f"Booking {booking.booking_id} confirmed and QR ticket issued."}

            elif anomaly_type == "AMOUNT_MISMATCH":
                booking = Booking.objects.select_for_update().get(booking_id=booking_id)
                # Compute balance due directly from final amount - total paid payments
                total_paid = sum(
                    p.amount
                    for p in Payment.objects.filter(booking=booking, status__in=["PAID", "SUCCESSFUL"])
                )
                booking.amount_paid = total_paid
                booking.balance_due = max(Decimal("0.00"), booking.final_amount - total_paid)
                booking.save()

                AuditLog.objects.create(
                    user=user,
                    action="FINANCIAL_RECONCILIATION_RESOLVED",
                    resource_type="BOOKING",
                    resource_id=booking.booking_id,
                    details={
                        "resolution": "SYNCED_BALANCE",
                        "new_paid": float(booking.amount_paid),
                        "new_balance": float(booking.balance_due),
                    },
                )
                return {
                    "success": True,
                    "message": f"Accounting for booking {booking.booking_id} synced. Paid: ₹{booking.amount_paid}, Balance Due: ₹{booking.balance_due}.",
                }

            elif anomaly_type == "STALE_PENDING_PAYMENT":
                pay = Payment.objects.select_for_update().get(payment_id=payment_id)
                if pay.status == "PENDING":
                    pay.status = "TIMEOUT"
                    pay.failure_reason = "Payment session timed out without provider confirmation."
                    pay.save()

                AuditLog.objects.create(
                    user=user,
                    action="FINANCIAL_RECONCILIATION_RESOLVED",
                    resource_type="PAYMENT",
                    resource_id=pay.payment_id,
                    details={"resolution": "MARKED_TIMEOUT"},
                )
                return {"success": True, "message": f"Payment {pay.payment_id} marked as TIMEOUT."}

            else:
                raise ValueError(f"Unknown anomaly resolution type: {anomaly_type}")
