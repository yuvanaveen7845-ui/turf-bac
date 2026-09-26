import uuid
from datetime import datetime, timedelta, date, time
from decimal import Decimal
from django.db import transaction
from django.utils import timezone
from .models import Booking
from turfs.models import Turf, TimeSlot
from payments.models import Payment
from promotions.models import Coupon, CouponUsage
from pricing.engine import PricingEngine
from qr_system.services import QRService
from notifications.models import Notification
from notifications.services import EmailNotificationService
from wallet.models import WalletTransaction
from audit.models import AuditLog
from accounts.settings_helper import BusinessSettingsHelper
from payments.cancellation import CancellationPolicyEngine


class BookingEngine:
    @classmethod
    def get_lock_duration_minutes(cls) -> int:
        return BusinessSettingsHelper.get_slot_lock_duration_minutes()

    # Fallback attribute for backward compatibility
    LOCK_DURATION_MINUTES = 5

    @classmethod
    def lock_slots(cls, turf, date_obj, slot_ids, user):
        """
        Temporarily locks slots during checkout with dynamic lock duration (default 5 mins)
        with atomic row-level locking.
        Validates contiguous time slots to ensure accurate match duration without gaps.
        Prevents race conditions and double-booking.
        """
        now = timezone.now()
        duration_mins = cls.get_lock_duration_minutes()
        lock_until = now + timedelta(minutes=duration_mins)

        if getattr(turf, "is_deleted", False) or not getattr(turf, "is_active", True):
            return False, f"Turf arena '{turf.name}' is currently unavailable for reservations."

        with transaction.atomic():
            # Use select_for_update to lock rows and prevent concurrent overwrites
            slots = list(
                TimeSlot.objects.select_for_update()
                .filter(id__in=slot_ids, turf=turf, date=date_obj)
                .order_by("start_time")
            )

            if len(slots) != len(slot_ids):
                return False, "One or more selected slots could not be found."

            # Verify contiguous / consecutive slots for multi-hour bookings
            if len(slots) > 1:
                for i in range(len(slots) - 1):
                    if slots[i].end_time != slots[i + 1].start_time:
                        return (
                            False,
                            f"Selected slots must be consecutive hours ({slots[i].start_time.strftime('%H:%M')}-{slots[i].end_time.strftime('%H:%M')} and {slots[i+1].start_time.strftime('%H:%M')}-{slots[i+1].end_time.strftime('%H:%M')} are not contiguous).",
                        )

            # Verify past/ongoing slot restrictions
            current_local = timezone.localtime(timezone.now())
            current_date = current_local.date()
            current_time = current_local.time()

            if date_obj < current_date:
                return False, "Cannot reserve time slots for a past date."

            booking_rules = BusinessSettingsHelper.get_booking_rules()
            max_advance_days = int(booking_rules.get("advanceBookingDays", 14))
            if (date_obj - current_date).days > max_advance_days:
                return False, f"Slots can only be reserved up to {max_advance_days} days in advance."

            for slot in slots:
                if date_obj == current_date and slot.start_time <= current_time:
                    return (
                        False,
                        f"Slot {slot.start_time.strftime('%I:%M %p')} has already started or passed. Please select a future time slot.",
                    )

            # Check all slots are either AVAILABLE or have expired locks
            for slot in slots:
                if slot.status == "BOOKED":
                    return (
                        False,
                        f"Slot {slot.start_time.strftime('%H:%M')} is already booked by another customer.",
                    )
                if slot.status == "MAINTENANCE":
                    return (
                        False,
                        f"Slot {slot.start_time.strftime('%H:%M')} is closed for scheduled maintenance.",
                    )
                if (
                    slot.status == "LOCKED"
                    and not slot.is_lock_expired()
                    and slot.locked_by != user
                ):
                    return (
                        False,
                        f"Slot {slot.start_time.strftime('%H:%M')} is temporarily held by another customer.",
                    )

            # Apply 5-minute temporary lock
            for slot in slots:
                slot.status = "LOCKED"
                slot.locked_until = lock_until
                slot.locked_by = user
                slot.save()

        return True, {
            "locked_until": lock_until.isoformat(),
            "expires_at": lock_until.isoformat(),
            "lock_duration_seconds": duration_mins * 60,
            "slot_ids": [str(s.id) for s in slots],
            "locked_slots": [
                {
                    "id": str(s.id),
                    "start_time": s.start_time.strftime("%H:%M"),
                    "end_time": s.end_time.strftime("%H:%M"),
                    "price": float(s.price),
                }
                for s in slots
            ],
        }

    @classmethod
    def unlock_slots(cls, turf, date_obj, slot_ids, user):
        """
        Explicitly releases temporary slot locks held by the user upon checkout cancellation.
        """
        with transaction.atomic():
            slots = TimeSlot.objects.select_for_update().filter(
                id__in=slot_ids,
                turf=turf,
                date=date_obj,
                status="LOCKED",
                locked_by=user,
            )
            count = slots.count()
            if count > 0:
                slots.update(status="AVAILABLE", locked_until=None, locked_by=None)
        return count

    @classmethod
    def release_expired_locks(cls):
        """Routine to atomically free expired temporary locks."""
        now = timezone.now()
        with transaction.atomic():
            expired = TimeSlot.objects.select_for_update().filter(
                status="LOCKED", locked_until__lt=now
            )
            count = expired.count()
            if count > 0:
                expired.update(status="AVAILABLE", locked_until=None, locked_by=None)
        return count

    @classmethod
    def create_booking(
        cls,
        turf,
        date_obj,
        slot_ids,
        user,
        booking_type="REGULAR",
        coupon_code=None,
        payment_type="FULL",
        payment_method="UPI",
        notes="",
        participants=None,
        collected_by=None,
    ):
        """
        Authoritative booking creation pipeline:
        - Transactional row-level lock (select_for_update)
        - Computes dynamic pricing & coupons server-side
        - Generates human-readable booking ID (FT-YY-XXXXXX)
        - Atomically saves Booking & marks slots BOOKED
        - Creates QR Ticket
        - Emits Loyalty points, Notification & Audit Log
        """
        if getattr(turf, "is_deleted", False) or not getattr(turf, "is_active", True):
            raise ValueError(f"Turf arena '{turf.name}' is currently unavailable for reservations.")

        with transaction.atomic():
            slots = list(
                TimeSlot.objects.select_for_update()
                .filter(id__in=slot_ids, turf=turf, date=date_obj)
                .order_by("start_time")
            )
            current_local = timezone.localtime(timezone.now())
            current_date = current_local.date()
            if date_obj < current_date:
                raise ValueError("Cannot book time slots for a past date.")

            booking_rules = BusinessSettingsHelper.get_booking_rules()
            max_advance_days = int(booking_rules.get("advanceBookingDays", 14))
            if (date_obj - current_date).days > max_advance_days:
                raise ValueError(f"Slots can only be booked up to {max_advance_days} days in advance.")

            if not slots or len(slots) != len(slot_ids):
                raise ValueError("Selected slots are invalid or no longer available.")

            # Strict availability and lock validation
            for slot in slots:
                if slot.status == "BOOKED":
                    raise ValueError(
                        f"Slot {slot.start_time.strftime('%H:%M')} is already booked."
                    )
                if slot.status == "MAINTENANCE":
                    raise ValueError(
                        f"Slot {slot.start_time.strftime('%H:%M')} is under maintenance."
                    )
                if (
                    slot.status == "LOCKED"
                    and not slot.is_lock_expired()
                    and slot.locked_by != user
                ):
                    raise ValueError(
                        f"Slot {slot.start_time.strftime('%H:%M')} is held by another user."
                    )
            # Feature Flag Validations
            if booking_type == "RECURRING" and not BusinessSettingsHelper.is_feature_enabled("RECURRING_BOOKINGS"):
                raise ValueError("Recurring squad & league bookings are currently disabled by administration.")
            if booking_type == "WALK_IN" and not BusinessSettingsHelper.is_feature_enabled("WALK_IN_BOOKINGS"):
                raise ValueError("Physical ground walk-in bookings are currently disabled by administration.")
            if payment_type == "PARTIAL" and not BusinessSettingsHelper.is_feature_enabled("PARTIAL_PAYMENTS"):
                raise ValueError("Split advance deposit payments are currently disabled.")
            if coupon_code and not BusinessSettingsHelper.is_feature_enabled("COUPONS"):
                raise ValueError("Promotions and coupons are currently disabled.")
            if payment_method in ["CASH", "OFFLINE"] and not BusinessSettingsHelper.is_feature_enabled("OFFLINE_PAYMENTS"):
                raise ValueError("Offline / cash payments are currently disabled.")
            if payment_method in ["RAZORPAY", "ONLINE"] and not BusinessSettingsHelper.is_feature_enabled("ONLINE_PAYMENTS"):
                raise ValueError("Online gateway payments are currently disabled.")

            # Validate coupon if given
            coupon = None
            if coupon_code:
                coupon = Coupon.objects.filter(code__iexact=coupon_code.strip()).first()

            slot_items = [
                {"start_time": s.start_time, "end_time": s.end_time} for s in slots
            ]
            price_data = PricingEngine.calculate_booking_total(
                turf=turf,
                date_obj=date_obj,
                slot_items=slot_items,
                coupon=coupon,
                user=user,
            )

            final_amt = Decimal(str(price_data["final_amount"]))
            if payment_type == "FULL":
                amt_paid = final_amt
                balance = Decimal("0.00")
                b_status = "CONFIRMED"
            elif payment_type == "PARTIAL":
                deposit_fraction = BusinessSettingsHelper.get_advance_deposit_fraction()
                amt_paid = round(final_amt * deposit_fraction, 2)
                balance = final_amt - amt_paid
                b_status = "CONFIRMED"
            else:
                amt_paid = Decimal("0.00")
                balance = final_amt
                b_status = "PAYMENT_PENDING"

            booking_id = Booking.generate_booking_id(date_obj)
            start_time = slots[0].start_time
            end_time = slots[-1].end_time

            booking = Booking.objects.create(
                booking_id=booking_id,
                customer=user,
                turf=turf,
                date=date_obj,
                start_time=start_time,
                end_time=end_time,
                booking_type=booking_type,
                status=b_status,
                total_amount=Decimal(str(price_data["subtotal"])),
                discount_amount=Decimal(str(price_data["total_discount"])),
                tax_amount=Decimal(str(price_data["tax_amount"])),
                final_amount=final_amt,
                amount_paid=amt_paid,
                balance_due=balance,
                coupon_code=coupon.code if coupon else "",
                pricing_breakdown=price_data,
                participants=participants or [],
                notes=notes,
            )

            # Mark slots as BOOKED and clear locks
            for slot in slots:
                slot.status = "BOOKED"
                slot.booking_id = booking.booking_id
                slot.locked_until = None
                slot.locked_by = None
                slot.save()
                booking.slots.add(slot)

            # Record coupon usage if applied
            if coupon:
                coupon.usage_count += 1
                coupon.save()
                CouponUsage.objects.create(
                    coupon=coupon,
                    user=user,
                    booking=booking,
                    discount_applied=Decimal(str(price_data["coupon_discount"])),
                )

            # Generate QR Ticket
            QRService.generate_qr_for_booking(booking)

            # Record Authoritative Initial Payment if money was collected (Cash, Spot UPI, Card, Wallet)
            if amt_paid > Decimal("0.00"):
                provider = "CASH" if payment_method == "CASH" else ("WALLET" if payment_method == "WALLET" else "RAZORPAY")
                Payment.objects.create(
                    payment_id=f"PAY_{uuid.uuid4().hex[:12].upper()}",
                    booking=booking,
                    customer=user,
                    provider=provider,
                    amount=amt_paid,
                    currency="INR",
                    payment_method=payment_method,
                    payment_type=payment_type,
                    transaction_reference=f"TXN-{booking.booking_id}-{uuid.uuid4().hex[:6].upper()}",
                    status="PAID",
                    collected_by=collected_by if (collected_by and getattr(collected_by, "is_authenticated", False)) else None,
                    gateway_response={"source": "booking_engine_initial", "notes": notes},
                )

            # Update customer profile stats
            if hasattr(user, "customer_profile") and amt_paid > 0:
                prof = user.customer_profile
                prof.total_bookings += 1
                prof.total_spending = Decimal(str(prof.total_spending)) + amt_paid
                prof.save()

            # Notification & Audit
            Notification.objects.create(
                user=user,
                notification_type="BOOKING_CONFIRMED",
                title=f"Pitch Booking Confirmed ({booking.booking_id})",
                message=f"Your pitch at {turf.name} on {date_obj.strftime('%d %b %Y')} ({start_time.strftime('%H:%M')}-{end_time.strftime('%H:%M')}) is confirmed.",
                data={"booking_id": booking.booking_id},
            )

            if b_status == "CONFIRMED":
                try:
                    EmailNotificationService.send_booking_confirmation_email(booking)
                except Exception:
                    pass

            AuditLog.objects.create(
                user=user,
                action="BOOKING_CREATED",
                resource_type="BOOKING",
                resource_id=booking.booking_id,
                details={
                    "final_amount": float(final_amt),
                    "turf": turf.name,
                    "payment_type": payment_type,
                    "booking_type": booking_type,
                },
            )

        return booking

    @classmethod
    def cancel_booking(cls, booking, user, reason=""):
        """
        Cancels booking atomically, releases slots, credits refund to customer wallet.
        """
        with transaction.atomic():
            # Lock booking and slots
            booking = Booking.objects.select_for_update().get(pk=booking.pk)
            booking.transition_to("CANCELLED")
            booking.cancelled_at = timezone.now()
            booking.cancel_reason = reason
            booking.save()

            # Release slots
            for slot in booking.slots.select_for_update():
                slot.status = "AVAILABLE"
                slot.booking_id = ""
                slot.locked_until = None
                slot.locked_by = None
                slot.save()

            # Process dynamic refund to customer wallet based on cancellation policy lead time
            cancellation_calc = CancellationPolicyEngine.calculate_refund(booking)
            refund_amount = Decimal(str(cancellation_calc["refundable_amount"]))
            cancellation_fee = Decimal(str(cancellation_calc["cancellation_fee"]))
            policy_rule = cancellation_calc.get("policy_rule", "Cancellation Policy")

            if refund_amount > 0:
                if hasattr(booking.customer, "customer_profile"):
                    prof = booking.customer.customer_profile
                    prof.wallet_balance = Decimal(str(prof.wallet_balance)) + refund_amount
                    prof.cancellation_count += 1
                    prof.save()

                    WalletTransaction.objects.create(
                        customer=booking.customer,
                        amount=refund_amount,
                        transaction_type="CREDIT",
                        source="REFUND",
                        reference_id=booking.booking_id,
                        description=f"Refund for cancelled match {booking.booking_id} ({policy_rule})",
                        balance_after=prof.wallet_balance,
                    )

                    # Create official Refund ledger record
                    try:
                        import uuid
                        from payments.models import Payment, Refund
                        payment = Payment.objects.filter(booking=booking, status__in=["PAID", "SUCCESSFUL"]).order_by("-created_at").first()
                        if not payment:
                            payment = Payment.objects.create(
                                payment_id=Payment.generate_payment_id(),
                                booking=booking,
                                customer=booking.customer,
                                provider="DIRECT",
                                amount=Decimal(str(booking.amount_paid or refund_amount)),
                                currency="INR",
                                payment_method="CASH" if booking.booking_type == "WALK_IN" else "UPI",
                                payment_type="FULL",
                                transaction_reference=f"RECON-{uuid.uuid4().hex[:8].upper()}",
                                status="PAID",
                                paid_at=booking.created_at,
                                completed_at=booking.created_at,
                                notes=f"Reconciled transaction synthesized during cancellation refund (#{booking.booking_id})",
                            )

                        refund_id = f"REF-{uuid.uuid4().hex[:8].upper()}"
                        Refund.objects.create(
                            refund_id=refund_id,
                            payment=payment,
                            booking=booking,
                            amount=refund_amount,
                            refund_type="FULL" if cancellation_fee == 0 else "PARTIAL",
                            refund_to="WALLET",
                            status="COMPLETED",
                            reason=f"Cancellation refund ({policy_rule}): {reason}",
                            reference_id=f"RREF-{uuid.uuid4().hex[:10].upper()}",
                            initiated_by=user if user and user.is_authenticated else None,
                            completed_at=timezone.now(),
                        )
                    except Exception as ref_err:
                        logger.warning(f"Could not persist ledger Refund row for {booking.booking_id}: {ref_err}")

            # Send Notification & Audit
            fee_note = f" (Fee of ₹{cancellation_fee} applied: {policy_rule})" if cancellation_fee > 0 else ""
            Notification.objects.create(
                user=booking.customer,
                notification_type="BOOKING_CANCELLED",
                title=f"Booking Cancelled ({booking.booking_id})",
                message=f"Booking for {booking.turf.name} on {booking.date} cancelled. Refund of ₹{refund_amount} credited to your wallet{fee_note}.",
                data={
                    "booking_id": booking.booking_id,
                    "refund_amount": float(refund_amount),
                    "cancellation_fee": float(cancellation_fee),
                    "policy_rule": policy_rule,
                },
            )

            AuditLog.objects.create(
                user=user,
                action="BOOKING_CANCELLED",
                resource_type="BOOKING",
                resource_id=booking.booking_id,
                details={
                    "reason": reason,
                    "refund": float(refund_amount),
                    "fee": float(cancellation_fee),
                    "policy": policy_rule,
                },
            )

        return booking

    @classmethod
    def reschedule_booking(cls, booking, new_date, new_slot_ids, user):
        """
        Atomically reschedules booking to a new date and time slots:
        - Locks both old and new slots
        - Recalculates new dynamic pricing & computes price difference
        - Adjusts balance due or wallet credit if price changes
        - Regenerates QR credential
        """
        with transaction.atomic():
            booking = Booking.objects.select_for_update().get(pk=booking.pk)
            if not booking.can_transition_to("CANCELLED"):
                raise ValueError(f"Cannot reschedule booking in {booking.status} status.")

            new_slots = list(
                TimeSlot.objects.select_for_update()
                .filter(id__in=new_slot_ids, turf=booking.turf, date=new_date)
                .order_by("start_time")
            )
            if not new_slots or len(new_slots) != len(new_slot_ids):
                raise ValueError("One or more requested slots on the new date are not available.")

            # Validate contiguous / consecutive hours for multi-slot reschedule
            if len(new_slots) > 1:
                for i in range(len(new_slots) - 1):
                    if new_slots[i].end_time != new_slots[i + 1].start_time:
                        raise ValueError(
                            f"Rescheduled slots must be consecutive hours ("
                            f"{new_slots[i].start_time.strftime('%H:%M')}-{new_slots[i].end_time.strftime('%H:%M')} and "
                            f"{new_slots[i+1].start_time.strftime('%H:%M')}-{new_slots[i+1].end_time.strftime('%H:%M')} are not contiguous)."
                        )

            for slot in new_slots:
                if slot.status == "BOOKED":
                    raise ValueError(
                        f"Slot {slot.start_time.strftime('%H:%M')} on {new_date} is already booked."
                    )
                if slot.status == "MAINTENANCE":
                    raise ValueError(
                        f"Slot {slot.start_time.strftime('%H:%M')} is under maintenance."
                    )

            # Release old slots
            for old_slot in booking.slots.select_for_update():
                old_slot.status = "AVAILABLE"
                old_slot.booking_id = ""
                old_slot.locked_until = None
                old_slot.locked_by = None
                old_slot.save()

            # Calculate price for new schedule
            slot_items = [{"start_time": s.start_time, "end_time": s.end_time} for s in new_slots]
            new_price_data = PricingEngine.calculate_booking_total(
                turf=booking.turf,
                date_obj=new_date,
                slot_items=slot_items,
                user=booking.customer,
            )
            new_final_amt = Decimal(str(new_price_data["final_amount"]))
            old_paid = booking.amount_paid

            if new_final_amt > old_paid:
                balance_due = new_final_amt - old_paid
            else:
                balance_due = Decimal("0.00")
                # Credit excess to wallet if new total is lower
                excess = old_paid - new_final_amt
                if excess > 0 and hasattr(booking.customer, "customer_profile"):
                    prof = booking.customer.customer_profile
                    prof.wallet_balance += excess
                    prof.save()
                    WalletTransaction.objects.create(
                        customer=booking.customer,
                        amount=excess,
                        transaction_type="CREDIT",
                        source="REFUND",
                        reference_id=booking.booking_id,
                        description=f"Reschedule rate adjustment credit for {booking.booking_id}",
                        balance_after=prof.wallet_balance,
                    )
                    booking.amount_paid = new_final_amt

            # Re-link slots
            booking.slots.clear()
            for slot in new_slots:
                slot.status = "BOOKED"
                slot.booking_id = booking.booking_id
                slot.save()
                booking.slots.add(slot)

            booking.date = new_date
            booking.start_time = new_slots[0].start_time
            booking.end_time = new_slots[-1].end_time
            booking.total_amount = Decimal(str(new_price_data["subtotal"]))
            booking.discount_amount = Decimal(str(new_price_data["total_discount"]))
            booking.tax_amount = Decimal(str(new_price_data["tax_amount"]))
            booking.final_amount = new_final_amt
            booking.balance_due = balance_due
            booking.pricing_breakdown = new_price_data
            booking.save()

            # Regenerate QR Pass
            QRService.generate_qr_for_booking(booking)

            # Notifications & Audit
            Notification.objects.create(
                user=booking.customer,
                notification_type="BOOKING_RESCHEDULED",
                title=f"Pitch Booking Rescheduled ({booking.booking_id})",
                message=f"Your booking for {booking.turf.name} has been moved to {new_date.strftime('%d %b %Y')} ({booking.start_time.strftime('%H:%M')}-{booking.end_time.strftime('%H:%M')}).",
                data={"booking_id": booking.booking_id},
            )

            AuditLog.objects.create(
                user=user,
                action="BOOKING_RESCHEDULED",
                resource_type="BOOKING",
                resource_id=booking.booking_id,
                details={
                    "new_date": str(new_date),
                    "new_start_time": str(booking.start_time),
                    "new_final_amount": float(new_final_amt),
                    "balance_due": float(balance_due),
                },
            )

        return booking

    @classmethod
    def record_offline_payment(
        cls, booking, amount, payment_method="CASH", reference_id="", collected_by=None
    ):
        """
        Records an offline in-person payment (Cash, UPI, Card, NetBanking) against a booking:
        - Atomically updates Payment and Booking financial states
        - Reduces balance due and marks booking CONFIRMED
        - Emits QR Ticket, Notifications & Audit Log
        """
        with transaction.atomic():
            booking = Booking.objects.select_for_update().get(pk=booking.pk)
            pay_amt = Decimal(str(amount))
            if pay_amt <= 0:
                raise ValueError("Payment amount must be greater than zero.")

            payment_id = Payment.generate_payment_id()
            txn_ref = reference_id or f"OFFLINE-{uuid.uuid4().hex[:10].upper()}"

            payment = Payment.objects.create(
                payment_id=payment_id,
                booking=booking,
                customer=booking.customer,
                provider="CASH" if payment_method == "CASH" else "RAZORPAY",
                amount=pay_amt,
                currency="INR",
                payment_method=payment_method,
                payment_type="BALANCE" if booking.amount_paid > 0 else "FULL",
                transaction_reference=txn_ref,
                status="PAID",
                paid_at=timezone.now(),
                completed_at=timezone.now(),
            )

            booking.amount_paid += pay_amt
            booking.balance_due = max(Decimal("0.00"), booking.final_amount - booking.amount_paid)
            if booking.can_transition_to("CONFIRMED"):
                booking.transition_to("CONFIRMED")
            booking.save()

            # Ensure all slots are permanently BOOKED
            for slot in booking.slots.all():
                slot.status = "BOOKED"
                slot.locked_until = None
                slot.locked_by = None
                slot.save()

            # Generate/Refresh QR Ticket
            QRService.generate_qr_for_booking(booking)

            # Notification & Audit
            collector_info = (
                f" by staff {collected_by.email}" if collected_by else ""
            )
            Notification.objects.create(
                user=booking.customer,
                notification_type="PAYMENT_RECEIVED",
                title=f"Payment Received: ₹{pay_amt} ({booking.booking_id})",
                message=f"Offline payment of ₹{pay_amt} via {payment_method} recorded{collector_info}. Balance due: ₹{booking.balance_due}.",
                data={"booking_id": booking.booking_id, "payment_id": payment.payment_id},
            )

            AuditLog.objects.create(
                user=collected_by or booking.customer,
                action="OFFLINE_PAYMENT_RECORDED",
                resource_type="PAYMENT",
                resource_id=payment.payment_id,
                details={
                    "booking_id": booking.booking_id,
                    "amount": float(pay_amt),
                    "method": payment_method,
                    "reference": txn_ref,
                },
            )

        return payment
