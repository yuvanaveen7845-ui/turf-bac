from datetime import datetime, timedelta, date, time
from decimal import Decimal
from django.db import transaction
from django.utils import timezone
from .models import Booking
from turfs.models import Turf, TimeSlot
from promotions.models import Coupon, CouponUsage, ReferralReward
from pricing.engine import PricingEngine
from qr_system.services import QRService
from notifications.models import Notification
from wallet.models import WalletTransaction, LoyaltyTransaction
from audit.models import AuditLog


class BookingEngine:
    LOCK_DURATION_MINUTES = 5

    @classmethod
    def lock_slots(cls, turf, date_obj, slot_ids, user):
        """
        Temporarily locks slots during checkout for 5 minutes.
        Prevents double-booking.
        """
        now = timezone.now()
        lock_until = now + timedelta(minutes=cls.LOCK_DURATION_MINUTES)

        slots = TimeSlot.objects.filter(id__in=slot_ids, turf=turf, date=date_obj)
        if len(slots) != len(slot_ids):
            return False, "One or more selected slots could not be found."

        # Check all slots are either AVAILABLE or expired locked
        for slot in slots:
            if slot.status == "BOOKED":
                return (
                    False,
                    f"Slot {slot.start_time.strftime('%H:%M')} is already booked.",
                )
            if slot.status == "MAINTENANCE":
                return (
                    False,
                    f"Slot {slot.start_time.strftime('%H:%M')} is under maintenance.",
                )
            if (
                slot.status == "LOCKED"
                and not slot.is_lock_expired()
                and slot.locked_by != user
            ):
                return (
                    False,
                    f"Slot {slot.start_time.strftime('%H:%M')} is temporarily reserved by another customer.",
                )

        # Lock all
        for slot in slots:
            slot.status = "LOCKED"
            slot.locked_until = lock_until
            slot.locked_by = user
            slot.save()

        return True, {
            "locked_until": lock_until.isoformat(),
            "slot_ids": [str(s.id) for s in slots],
        }

    @classmethod
    def release_expired_locks(cls):
        """Background routine to free expired temporary locks."""
        now = timezone.now()
        expired = TimeSlot.objects.filter(status="LOCKED", locked_until__lt=now)
        count = expired.count()
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
    ):
        """
        Core booking creation method:
        - Validates slots & availability
        - Computes dynamic pricing & coupons
        - Atomically saves Booking & marks slots BOOKED
        - Creates QR Ticket
        - Creates In-App Notification
        """
        slots = list(
            TimeSlot.objects.filter(id__in=slot_ids, turf=turf, date=date_obj).order_by(
                "start_time"
            )
        )
        if not slots:
            raise ValueError("No valid slots selected.")

        # Availability / lock validation
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
                    f"Slot {slot.start_time.strftime('%H:%M')} is locked by another customer."
                )

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
            amt_paid = round(final_amt * Decimal("0.30"), 2)  # 30% advance
            balance = final_amt - amt_paid
            b_status = "CONFIRMED"
        else:
            amt_paid = Decimal("0.00")
            balance = final_amt
            b_status = "PAYMENT_PENDING"

        # Generate unique Booking ID
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

        # Mark slots as BOOKED
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
        qr_ticket = QRService.generate_qr_for_booking(booking)

        # Update customer profile stats
        if hasattr(user, "customer_profile"):
            prof = user.customer_profile
            prof.total_bookings += 1
            prof.total_spending = Decimal(str(prof.total_spending)) + Decimal(
                str(amt_paid)
            )
            # Earn 5% loyalty points on payment
            points_earned = int(Decimal(str(amt_paid)) * Decimal("0.05"))
            if points_earned > 0:
                prof.loyalty_points += points_earned
                LoyaltyTransaction.objects.create(
                    customer=user,
                    points=points_earned,
                    transaction_type="EARN",
                    source="BOOKING",
                    reference_id=booking.booking_id,
                    description=f"Earned from booking {booking.booking_id}",
                    balance_after=prof.loyalty_points,
                )
            prof.save()

        # Send in-app notification
        Notification.objects.create(
            user=user,
            notification_type="BOOKING_CONFIRMED",
            title=f"Booking Confirmed! {booking.booking_id}",
            message=f"Your booking for {turf.name} on {date_obj.strftime('%d %b %Y')} ({start_time.strftime('%H:%M')}-{end_time.strftime('%H:%M')}) is confirmed.",
            data={"booking_id": booking.booking_id},
        )

        # Audit log
        AuditLog.objects.create(
            user=user,
            action="BOOKING_CREATED",
            resource_type="BOOKING",
            resource_id=booking.booking_id,
            details={"final_amount": float(final_amt), "turf": turf.name},
        )

        return booking

    @classmethod
    def cancel_booking(cls, booking, user, reason=""):
        """
        Cancels a booking, releases slots, credits refund to customer wallet.
        """
        if booking.status in ("CANCELLED", "COMPLETED", "CHECKED_IN", "IN_PROGRESS"):
            raise ValueError(f"Cannot cancel booking with status: {booking.status}")

        booking.status = "CANCELLED"
        booking.cancelled_at = timezone.now()
        booking.cancel_reason = reason
        booking.save()

        # Free all slots
        for slot in booking.slots.all():
            slot.status = "AVAILABLE"
            slot.booking_id = ""
            slot.locked_until = None
            slot.locked_by = None
            slot.save()

        # Process refund to customer wallet if paid
        refund_amount = Decimal(str(booking.amount_paid))
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
                    description=f"Refund for cancelled booking {booking.booking_id}",
                    balance_after=prof.wallet_balance,
                )

        Notification.objects.create(
            user=booking.customer,
            notification_type="BOOKING_CANCELLED",
            title=f"Booking Cancelled {booking.booking_id}",
            message=f"Booking for {booking.turf.name} has been cancelled. Refund ₹{refund_amount} credited to your wallet.",
            data={
                "booking_id": booking.booking_id,
                "refund_amount": float(refund_amount),
            },
        )

        AuditLog.objects.create(
            user=user,
            action="BOOKING_CANCELLED",
            resource_type="BOOKING",
            resource_id=booking.booking_id,
            details={"reason": reason, "refund": float(refund_amount)},
        )

        return booking

    @classmethod
    def reschedule_booking(cls, booking, new_date, new_slot_ids, user):
        """
        Atomically reschedules booking to a new date and time slots.
        """
        if booking.status not in ("CONFIRMED", "UPCOMING"):
            raise ValueError(f"Cannot reschedule booking in {booking.status} status.")

        new_slots = list(
            TimeSlot.objects.filter(
                id__in=new_slot_ids, turf=booking.turf, date=new_date
            ).order_by("start_time")
        )
        if not new_slots:
            raise ValueError("No available slots found for the requested date.")

        for slot in new_slots:
            if slot.status == "BOOKED":
                raise ValueError(
                    f"Slot {slot.start_time.strftime('%H:%M')} on {new_date} is already booked."
                )

        # Release old slots
        for old_slot in booking.slots.all():
            old_slot.status = "AVAILABLE"
            old_slot.booking_id = ""
            old_slot.save()

        # Clear old slots relation and attach new ones
        booking.slots.clear()
        for slot in new_slots:
            slot.status = "BOOKED"
            slot.booking_id = booking.booking_id
            slot.save()
            booking.slots.add(slot)

        booking.date = new_date
        booking.start_time = new_slots[0].start_time
        booking.end_time = new_slots[-1].end_time
        booking.save()

        # Regenerate QR Ticket with new schedule
        QRService.generate_qr_for_booking(booking)

        Notification.objects.create(
            user=booking.customer,
            notification_type="BOOKING_RESCHEDULED",
            title=f"Booking Rescheduled {booking.booking_id}",
            message=f"Your match has been moved to {new_date.strftime('%d %b %Y')} ({booking.start_time.strftime('%H:%M')}-{booking.end_time.strftime('%H:%M')}).",
            data={"booking_id": booking.booking_id},
        )

        return booking
