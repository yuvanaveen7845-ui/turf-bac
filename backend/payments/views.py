import csv
import json
import uuid
from decimal import Decimal
from datetime import datetime, timedelta
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.db import transaction, models
from django.http import HttpResponse
from django.conf import settings
from rest_framework import status, views, permissions
from rest_framework.response import Response

from .models import Payment, Refund, DailyCashDrawer
from .serializers import PaymentSerializer, RefundSerializer, DailyCashDrawerSerializer
from .razorpay_client import RazorpayService
from .receipts import ReceiptGenerator
from .reconciliation import ReconciliationEngine
from .cancellation import CancellationPolicyEngine
from bookings.models import Booking
from bookings.serializers import BookingSerializer
from turfs.models import Turf, TimeSlot
from pricing.engine import PricingEngine
from promotions.models import Coupon, CouponUsage
from qr_system.services import QRService
from notifications.models import Notification
from notifications.services import EmailNotificationService
from wallet.models import WalletTransaction
from audit.models import AuditLog
from accounts.permissions import (
    IsAdmin,
    IsManager,
    IsStaffOrAdmin,
    CanProcessRefunds,
)
from realtime.events import publish_event


class CreateRazorpayOrderView(views.APIView):
    """
    Creates a server-calculated Razorpay Order:
    - Validates slot availability
    - Applies 5-minute temporary slot lock
    - Computes exact pricing server-side (ignores any client price tampering)
    - Creates Booking in PAYMENT_PENDING status (or reuses existing held booking)
    - Creates Razorpay Order & Payment record in PENDING status
    """
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        turf_id = request.data.get("turf_id")
        date_str = request.data.get("date")
        slot_ids = request.data.get("slot_ids", [])
        coupon_code = request.data.get("coupon_code", "").strip()
        payment_type = request.data.get("payment_type", "FULL")
        notes = request.data.get("notes", "")
        participants = request.data.get("participants", [])

        if not turf_id or not date_str or not slot_ids:
            return Response(
                {"error": "turf_id, date, and slot_ids are required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        turf = get_object_or_404(Turf, pk=turf_id)
        try:
            date_obj = timezone.datetime.strptime(date_str, "%Y-%m-%d").date()
        except ValueError:
            return Response(
                {"error": "Invalid date format. Use YYYY-MM-DD."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        slots = list(
            TimeSlot.objects.filter(id__in=slot_ids, turf=turf, date=date_obj).order_by(
                "start_time"
            )
        )
        if len(slots) != len(slot_ids):
            return Response(
                {"error": "One or more selected slots are invalid."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Validate slot availability and locks
        now = timezone.now()
        local_now = timezone.localtime(now)
        local_date = local_now.date()
        local_time = local_now.time()

        if date_obj < local_date:
            return Response(
                {"error": "Cannot reserve time slots for a past date."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        for slot in slots:
            if date_obj == local_date and slot.start_time <= local_time:
                return Response(
                    {
                        "error": f"Slot {slot.start_time.strftime('%I:%M %p')} has already started or ended. Please choose an upcoming slot."
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )

        lock_until = now + timezone.timedelta(minutes=5)

        for slot in slots:
            if slot.status == "BOOKED":
                return Response(
                    {"error": f"Slot {slot.start_time.strftime('%H:%M')} is already booked."},
                    status=status.HTTP_409_CONFLICT,
                )
            if slot.status == "MAINTENANCE":
                return Response(
                    {"error": f"Slot {slot.start_time.strftime('%H:%M')} is under maintenance."},
                    status=status.HTTP_409_CONFLICT,
                )
            if (
                slot.status == "LOCKED"
                and not slot.is_lock_expired()
                and slot.locked_by != request.user
            ):
                return Response(
                    {"error": f"Slot {slot.start_time.strftime('%H:%M')} is held by another user."},
                    status=status.HTTP_409_CONFLICT,
                )

        # Apply 5-minute lock on slots
        for slot in slots:
            slot.status = "LOCKED"
            slot.locked_until = lock_until
            slot.locked_by = request.user
            slot.save()

        # Compute accurate server-side pricing
        coupon = None
        if coupon_code:
            coupon = Coupon.objects.filter(code__iexact=coupon_code).first()

        slot_items = [{"start_time": s.start_time, "end_time": s.end_time} for s in slots]
        price_data = PricingEngine.calculate_booking_total(
            turf=turf,
            date_obj=date_obj,
            slot_items=slot_items,
            coupon=coupon,
            user=request.user,
        )

        final_amt = Decimal(str(price_data["final_amount"]))
        if payment_type == "FULL":
            amount_to_charge = final_amt
        else:
            # 50% partial deposit
            amount_to_charge = round(final_amt * Decimal("0.50"), 2)

        booking_id = Booking.generate_booking_id(date_obj)
        booking = Booking.objects.create(
            booking_id=booking_id,
            customer=request.user,
            turf=turf,
            date=date_obj,
            start_time=slots[0].start_time,
            end_time=slots[-1].end_time,
            booking_type="REGULAR",
            status="PAYMENT_PENDING",
            total_amount=Decimal(str(price_data["subtotal"])),
            discount_amount=Decimal(str(price_data["total_discount"])),
            tax_amount=Decimal(str(price_data["tax_amount"])),
            final_amount=final_amt,
            amount_paid=Decimal("0.00"),
            balance_due=final_amt,
            coupon_code=coupon.code if coupon else "",
            pricing_breakdown=price_data,
            participants=participants or [],
            notes=notes,
        )

        # Attach slots to booking
        for slot in slots:
            slot.booking_id = booking.booking_id
            slot.save()
            booking.slots.add(slot)

        # Create Razorpay Order server-side
        try:
            rzp_order = RazorpayService.create_order(
                amount_in_rupees=amount_to_charge,
                receipt_id=booking.booking_id,
                notes={
                    "booking_id": booking.booking_id,
                    "customer_id": str(request.user.id),
                    "turf_name": turf.name,
                },
            )
        except Exception as e:
            return Response(
                {"error": f"Failed to initialize payment gateway: {str(e)}"},
                status=status.HTTP_502_BAD_GATEWAY,
            )

        payment_id = Payment.generate_payment_id()
        txn_ref = f"TXN-{uuid.uuid4().hex[:12].upper()}"

        payment = Payment.objects.create(
            payment_id=payment_id,
            booking=booking,
            customer=request.user,
            provider="RAZORPAY",
            provider_order_id=rzp_order["order_id"],
            amount=amount_to_charge,
            currency="INR",
            payment_method="UPI",
            payment_type=payment_type,
            transaction_reference=txn_ref,
            status="PENDING",
        )

        AuditLog.objects.create(
            user=request.user,
            action="PAYMENT_ORDER_CREATED",
            resource_type="PAYMENT",
            resource_id=payment.payment_id,
            details={
                "order_id": rzp_order["order_id"],
                "amount": float(amount_to_charge),
                "booking_id": booking.booking_id,
            },
        )

        return Response(
            {
                "order_id": rzp_order["order_id"],
                "amount": rzp_order["amount"],
                "currency": rzp_order["currency"],
                "key_id": rzp_order["key_id"],
                "booking_id": booking.booking_id,
                "amount_to_pay": float(amount_to_charge),
                "final_amount": float(booking.final_amount),
                "payment_id": payment.payment_id,
                "locked_until": lock_until.isoformat(),
            },
            status=status.HTTP_201_CREATED,
        )


class VerifyRazorpayPaymentView(views.APIView):
    """
    Verifies Razorpay HMAC-SHA256 signature and confirms booking:
    - Verifies order_id, payment_id, signature
    - Atomically updates Payment to PAID
    - Transitions Booking to CONFIRMED and marks slots BOOKED
    - Generates QR ticket pass & sends notification
    - Enforces idempotency against duplicate verification requests
    """
    permission_classes = [permissions.IsAuthenticated]

    @transaction.atomic
    def post(self, request):
        razorpay_order_id = request.data.get("razorpay_order_id")
        razorpay_payment_id = request.data.get("razorpay_payment_id")
        razorpay_signature = request.data.get("razorpay_signature")
        booking_id = request.data.get("booking_id")

        if not razorpay_order_id or not razorpay_payment_id or not booking_id:
            return Response(
                {"error": "razorpay_order_id, razorpay_payment_id, and booking_id are required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        booking = get_object_or_404(Booking, booking_id=booking_id)
        if request.user.role == "CUSTOMER" and booking.customer != request.user:
            return Response(
                {"error": "Unauthorized access to this booking."},
                status=status.HTTP_403_FORBIDDEN,
            )

        payment = Payment.objects.select_for_update().filter(
            booking=booking, provider_order_id=razorpay_order_id
        ).first()

        if not payment:
            payment = Payment.objects.select_for_update().filter(booking=booking).order_by("-created_at").first()

        if not payment:
            return Response(
                {"error": "No payment intent found for this booking order."},
                status=status.HTTP_404_NOT_FOUND,
            )

        # Idempotency check: if already PAID, return clean success immediately
        if payment.status in ("PAID", "SUCCESSFUL"):
            return Response(
                {
                    "status": "SUCCESS",
                    "message": "Payment was already verified and booking is confirmed.",
                    "booking": BookingSerializer(booking).data,
                    "payment": PaymentSerializer(payment).data,
                },
                status=status.HTTP_200_OK,
            )

        # Verify cryptographic signature via HMAC-SHA256
        is_valid = RazorpayService.verify_payment_signature(
            razorpay_order_id=razorpay_order_id,
            razorpay_payment_id=razorpay_payment_id,
            razorpay_signature=razorpay_signature,
        )

        if not is_valid:
            payment.status = "FAILED"
            payment.failure_reason = "Cryptographic signature verification failed."
            payment.save()
            return Response(
                {"error": "Payment signature verification failed. Booking not confirmed."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Payment Verified Successfully
        now = timezone.now()
        payment.status = "PAID"
        payment.provider_payment_id = razorpay_payment_id
        payment.provider_signature = razorpay_signature or ""
        payment.paid_at = now
        payment.completed_at = now
        payment.save()

        # Update Booking state to CONFIRMED
        booking.amount_paid += payment.amount
        booking.balance_due = max(Decimal("0.00"), booking.final_amount - booking.amount_paid)
        booking.status = "CONFIRMED"
        booking.save()

        # Mark all slots permanently BOOKED
        for slot in booking.slots.all():
            slot.status = "BOOKED"
            slot.locked_until = None
            slot.locked_by = None
            slot.save()

        # Record coupon usage if coupon applied
        if booking.coupon_code:
            coupon = Coupon.objects.filter(code__iexact=booking.coupon_code).first()
            if coupon:
                coupon.usage_count += 1
                coupon.save()
                CouponUsage.objects.create(
                    coupon=coupon,
                    user=booking.customer,
                    booking=booking,
                    discount_applied=booking.discount_amount,
                )

        # Generate Cryptographic QR Ticket
        QRService.generate_qr_for_booking(booking)

        # Update Customer profile spending
        if hasattr(booking.customer, "customer_profile"):
            prof = booking.customer.customer_profile
            prof.total_bookings += 1
            prof.total_spending = Decimal(str(prof.total_spending)) + Decimal(str(payment.amount))
            prof.save()

        # Send in-app notification
        Notification.objects.create(
            user=booking.customer,
            notification_type="BOOKING_CONFIRMED",
            title=f"Match Pass Confirmed! ({booking.booking_id})",
            message=f"Payment of ₹{payment.amount} verified. Your pitch at {booking.turf.name} is confirmed for {booking.date}.",
            data={"booking_id": booking.booking_id, "payment_id": payment.payment_id},
        )

        # Dispatch branded Match Pass email via Django SMTP
        try:
            EmailNotificationService.send_booking_confirmation_email(booking)
        except Exception as e:
            pass

        AuditLog.objects.create(
            user=booking.customer,
            action="PAYMENT_VERIFIED",
            resource_type="PAYMENT",
            resource_id=payment.payment_id,
            details={
                "provider_payment_id": razorpay_payment_id,
                "amount": float(payment.amount),
                "booking_id": booking.booking_id,
            },
        )

        return Response(
            {
                "status": "SUCCESS",
                "message": "Payment verified successfully and booking confirmed!",
                "booking": BookingSerializer(booking).data,
                "payment": PaymentSerializer(payment).data,
            },
            status=status.HTTP_200_OK,
        )


class CreateBalanceRazorpayOrderView(views.APIView):
    """
    Creates a Razorpay Order specifically for clearing the remaining balance due on a booking.
    """
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        booking_id = request.data.get("booking_id")
        if not booking_id:
            return Response(
                {"error": "booking_id is required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        booking = get_object_or_404(Booking, booking_id=booking_id)
        if request.user.role == "CUSTOMER" and booking.customer != request.user:
            return Response(
                {"error": "Unauthorized access to this booking."},
                status=status.HTTP_403_FORBIDDEN,
            )

        if booking.balance_due <= Decimal("0.00"):
            return Response(
                {"error": "This booking has no outstanding balance due."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        amount_to_charge = booking.balance_due
        receipt_id = f"BAL-{booking.booking_id[-8:]}"

        try:
            rzp_order = RazorpayService.create_order(
                amount_in_rupees=amount_to_charge,
                receipt_id=receipt_id,
                notes={
                    "type": "BALANCE_PAYMENT",
                    "booking_id": booking.booking_id,
                    "customer_id": str(request.user.id),
                    "turf_name": booking.turf.name,
                },
            )
        except Exception as e:
            return Response(
                {"error": f"Failed to initialize balance payment gateway: {str(e)}"},
                status=status.HTTP_502_BAD_GATEWAY,
            )

        payment_id = Payment.generate_payment_id()
        txn_ref = f"TXN-{uuid.uuid4().hex[:12].upper()}"

        payment = Payment.objects.create(
            payment_id=payment_id,
            booking=booking,
            customer=request.user,
            provider="RAZORPAY",
            provider_order_id=rzp_order["order_id"],
            amount=amount_to_charge,
            currency="INR",
            payment_method="UPI",
            payment_type="BALANCE",
            transaction_reference=txn_ref,
            status="PENDING",
            notes=f"Remaining Balance settlement for booking {booking.booking_id}",
        )

        AuditLog.objects.create(
            user=request.user,
            action="BALANCE_ORDER_CREATED",
            resource_type="PAYMENT",
            resource_id=payment.payment_id,
            details={
                "order_id": rzp_order["order_id"],
                "amount": float(amount_to_charge),
                "booking_id": booking.booking_id,
            },
        )

        return Response(
            {
                "order_id": rzp_order["order_id"],
                "amount": rzp_order["amount"],
                "currency": rzp_order["currency"],
                "key_id": rzp_order["key_id"],
                "booking_id": booking.booking_id,
                "amount_to_pay": float(amount_to_charge),
                "balance_due": float(booking.balance_due),
                "payment_id": payment.payment_id,
            },
            status=status.HTTP_201_CREATED,
        )


class VerifyBalanceRazorpayPaymentView(views.APIView):
    """
    Verifies Razorpay payment for remaining balance and settles booking balance to zero.
    """
    permission_classes = [permissions.IsAuthenticated]

    @transaction.atomic
    def post(self, request):
        razorpay_order_id = request.data.get("razorpay_order_id")
        razorpay_payment_id = request.data.get("razorpay_payment_id")
        razorpay_signature = request.data.get("razorpay_signature")
        booking_id = request.data.get("booking_id")

        if not razorpay_order_id or not razorpay_payment_id or not booking_id:
            return Response(
                {"error": "razorpay_order_id, razorpay_payment_id, and booking_id are required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        booking = get_object_or_404(Booking, booking_id=booking_id)
        if request.user.role == "CUSTOMER" and booking.customer != request.user:
            return Response(
                {"error": "Unauthorized access to this booking."},
                status=status.HTTP_403_FORBIDDEN,
            )

        payment = Payment.objects.select_for_update().filter(
            booking=booking, provider_order_id=razorpay_order_id
        ).first()

        if not payment:
            return Response(
                {"error": "No payment intent found for this order."},
                status=status.HTTP_404_NOT_FOUND,
            )

        if payment.status in ("PAID", "SUCCESSFUL"):
            return Response(
                {
                    "status": "SUCCESS",
                    "message": "Balance payment was already verified.",
                    "booking": BookingSerializer(booking).data,
                    "payment": PaymentSerializer(payment).data,
                },
                status=status.HTTP_200_OK,
            )

        is_valid = RazorpayService.verify_payment_signature(
            razorpay_order_id=razorpay_order_id,
            razorpay_payment_id=razorpay_payment_id,
            razorpay_signature=razorpay_signature,
        )

        if not is_valid:
            payment.status = "FAILED"
            payment.failure_reason = "Cryptographic signature verification failed."
            payment.save()
            return Response(
                {"error": "Payment signature verification failed."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        now = timezone.now()
        payment.status = "PAID"
        payment.provider_payment_id = razorpay_payment_id
        payment.provider_signature = razorpay_signature or ""
        payment.paid_at = now
        payment.completed_at = now
        payment.save()

        # Update booking amounts
        booking.amount_paid += payment.amount
        booking.balance_due = max(Decimal("0.00"), booking.final_amount - booking.amount_paid)
        booking.save()

        # Refresh QR Pass
        QRService.generate_qr_for_booking(booking)

        Notification.objects.create(
            user=booking.customer,
            notification_type="BOOKING_CONFIRMED",
            title=f"Balance Settle Confirmed ({booking.booking_id})",
            message=f"Remaining balance of ₹{payment.amount} has been paid via Razorpay. Your match pass is now 100% settled.",
            data={"booking_id": booking.booking_id, "payment_id": payment.payment_id},
        )

        AuditLog.objects.create(
            user=request.user,
            action="BALANCE_PAYMENT_VERIFIED",
            resource_type="PAYMENT",
            resource_id=payment.payment_id,
            details={
                "provider_payment_id": razorpay_payment_id,
                "amount": float(payment.amount),
                "booking_id": booking.booking_id,
            },
        )

        publish_event(
            channel="operations",
            event_type="OPERATIONS_UPDATE",
            payload={
                "type": "BALANCE_PAID",
                "booking_id": booking.booking_id,
                "amount": float(payment.amount),
                "balance_due": float(booking.balance_due),
            },
        )

        return Response(
            {
                "status": "SUCCESS",
                "message": "Balance payment verified successfully! Booking is fully paid.",
                "booking": BookingSerializer(booking).data,
                "payment": PaymentSerializer(payment).data,
            },
            status=status.HTTP_200_OK,
        )


class WalletBookingPaymentView(views.APIView):
    """
    Instant 1-Click checkout using Turf Cash Wallet balance:
    - Atomically checks customer wallet balance
    - Debits wallet and creates WalletTransaction record
    - Creates Payment record (PAID, provider=WALLET)
    - Confirms booking and generates Match Pass
    """
    permission_classes = [permissions.IsAuthenticated]

    @transaction.atomic
    def post(self, request):
        turf_id = request.data.get("turf_id")
        date_str = request.data.get("date")
        slot_ids = request.data.get("slot_ids", [])
        coupon_code = request.data.get("coupon_code", "").strip()
        notes = request.data.get("notes", "")

        if not turf_id or not date_str or not slot_ids:
            return Response(
                {"error": "turf_id, date, and slot_ids are required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        turf = get_object_or_404(Turf, pk=turf_id)
        try:
            date_obj = timezone.datetime.strptime(date_str, "%Y-%m-%d").date()
        except ValueError:
            return Response(
                {"error": "Invalid date format. Use YYYY-MM-DD."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        slots = list(
            TimeSlot.objects.filter(id__in=slot_ids, turf=turf, date=date_obj).order_by("start_time")
        )
        if len(slots) != len(slot_ids):
            return Response(
                {"error": "One or more selected slots are invalid."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        for slot in slots:
            if slot.status == "BOOKED":
                return Response(
                    {"error": f"Slot {slot.start_time.strftime('%H:%M')} is already booked."},
                    status=status.HTTP_409_CONFLICT,
                )
            if slot.status == "MAINTENANCE":
                return Response(
                    {"error": f"Slot {slot.start_time.strftime('%H:%M')} is under maintenance."},
                    status=status.HTTP_409_CONFLICT,
                )
            if slot.status == "LOCKED" and not slot.is_lock_expired() and slot.locked_by != request.user:
                return Response(
                    {"error": f"Slot {slot.start_time.strftime('%H:%M')} is held by another user."},
                    status=status.HTTP_409_CONFLICT,
                )

        # Price calculation
        coupon = None
        if coupon_code:
            coupon = Coupon.objects.filter(code__iexact=coupon_code).first()

        slot_items = [{"start_time": s.start_time, "end_time": s.end_time} for s in slots]
        price_data = PricingEngine.calculate_booking_total(
            turf=turf,
            date_obj=date_obj,
            slot_items=slot_items,
            coupon=coupon,
            user=request.user,
        )

        final_amt = Decimal(str(price_data["final_amount"]))

        # Check wallet balance
        if not hasattr(request.user, "customer_profile"):
            return Response(
                {"error": "Customer wallet profile not found."},
                status=status.HTTP_404_NOT_FOUND,
            )

        prof = request.user.customer_profile
        if prof.wallet_balance < final_amt:
            return Response(
                {
                    "error": f"Insufficient wallet balance (₹{prof.wallet_balance}). Required: ₹{final_amt}."
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Debit wallet atomically
        prof.wallet_balance -= final_amt
        prof.save()

        booking_id = Booking.generate_booking_id(date_obj)
        booking = Booking.objects.create(
            booking_id=booking_id,
            customer=request.user,
            turf=turf,
            date=date_obj,
            start_time=slots[0].start_time,
            end_time=slots[-1].end_time,
            booking_type="REGULAR",
            status="CONFIRMED",
            total_amount=Decimal(str(price_data["subtotal"])),
            discount_amount=Decimal(str(price_data["total_discount"])),
            tax_amount=Decimal(str(price_data["tax_amount"])),
            final_amount=final_amt,
            amount_paid=final_amt,
            balance_due=Decimal("0.00"),
            coupon_code=coupon.code if coupon else "",
            pricing_breakdown=price_data,
            notes=notes,
        )

        for slot in slots:
            slot.status = "BOOKED"
            slot.locked_until = None
            slot.locked_by = None
            slot.booking_id = booking.booking_id
            slot.save()
            booking.slots.add(slot)

        payment_id = Payment.generate_payment_id()
        txn_ref = f"WAL-{uuid.uuid4().hex[:10].upper()}"
        payment = Payment.objects.create(
            payment_id=payment_id,
            booking=booking,
            customer=request.user,
            provider="WALLET",
            amount=final_amt,
            currency="INR",
            payment_method="WALLET",
            payment_type="FULL",
            transaction_reference=txn_ref,
            status="PAID",
            paid_at=timezone.now(),
            completed_at=timezone.now(),
            notes="1-Click Instant Turf Cash Wallet Checkout",
        )

        WalletTransaction.objects.create(
            customer=request.user,
            amount=final_amt,
            transaction_type="DEBIT",
            source="BOOKING",
            reference_id=booking.booking_id,
            description=f"Slot Booking at {turf.name} ({booking.booking_id})",
            balance_after=prof.wallet_balance,
        )

        if coupon:
            coupon.usage_count += 1
            coupon.save()
            CouponUsage.objects.create(
                coupon=coupon,
                user=booking.customer,
                booking=booking,
                discount_applied=booking.discount_amount,
            )

        QRService.generate_qr_for_booking(booking)

        Notification.objects.create(
            user=booking.customer,
            notification_type="BOOKING_CONFIRMED",
            title=f"Instant Match Pass Confirmed! ({booking.booking_id})",
            message=f"₹{final_amt} debited from Turf Cash Wallet. Your pitch at {turf.name} is confirmed!",
            data={"booking_id": booking.booking_id, "payment_id": payment.payment_id},
        )

        # Dispatch branded Match Pass email via Django SMTP
        try:
            EmailNotificationService.send_booking_confirmation_email(booking)
        except Exception as e:
            pass

        AuditLog.objects.create(
            user=request.user,
            action="WALLET_BOOKING_COMPLETED",
            resource_type="BOOKING",
            resource_id=booking.booking_id,
            details={"amount": float(final_amt), "payment_id": payment.payment_id},
        )

        return Response(
            {
                "status": "SUCCESS",
                "message": "Booking confirmed instantly with Turf Cash Wallet!",
                "booking": BookingSerializer(booking).data,
                "payment": PaymentSerializer(payment).data,
                "new_wallet_balance": float(prof.wallet_balance),
            },
            status=status.HTTP_201_CREATED,
        )


class RazorpayWebhookView(views.APIView):
    """
    Idempotent Razorpay Webhook receiver:
    - Verifies X-Razorpay-Signature using RAZORPAY_WEBHOOK_SECRET
    - Handles payment.captured, order.paid, payment.failed, refund.processed
    - Ensures resilient confirmation even if customer closes browser
    - Ensures single transaction processing per webhook event
    """
    permission_classes = [permissions.AllowAny]

    def post(self, request):
        signature = request.headers.get("X-Razorpay-Signature", "")
        body_bytes = request.body

        # Verify webhook signature if secret configured
        webhook_secret = getattr(settings, "RAZORPAY_WEBHOOK_SECRET", "")
        if webhook_secret:
            is_valid = RazorpayService.verify_webhook_signature(body_bytes, signature)
            if not is_valid:
                return Response(
                    {"error": "Invalid webhook signature"},
                    status=status.HTTP_400_BAD_REQUEST,
                )

        try:
            payload = json.loads(body_bytes.decode("utf-8"))
        except Exception:
            return Response({"error": "Invalid JSON"}, status=status.HTTP_400_BAD_REQUEST)

        event = payload.get("event")
        event_id = payload.get("id", f"EVT-{uuid.uuid4().hex[:10]}")

        # Idempotency check
        existing_log = AuditLog.objects.filter(resource_id=event_id, action="WEBHOOK_PROCESSED").first()
        if existing_log:
            return Response({"status": "already_processed"}, status=status.HTTP_200_OK)

        with transaction.atomic():
            if event in ("payment.captured", "order.paid"):
                payment_entity = payload.get("payload", {}).get("payment", {}).get("entity", {})
                rzp_payment_id = payment_entity.get("id")
                rzp_order_id = payment_entity.get("order_id")
                amount = Decimal(str(payment_entity.get("amount", 0))) / Decimal("100.00")

                payment = Payment.objects.select_for_update().filter(provider_order_id=rzp_order_id).first()
                if payment and payment.status not in ("PAID", "SUCCESSFUL"):
                    payment.status = "PAID"
                    payment.provider_payment_id = rzp_payment_id
                    payment.paid_at = timezone.now()
                    payment.completed_at = timezone.now()
                    payment.save()

                    if payment.booking:
                        booking = payment.booking
                        booking.amount_paid += amount
                        booking.balance_due = max(Decimal("0.00"), booking.final_amount - booking.amount_paid)
                        booking.status = "CONFIRMED"
                        booking.save()

                        for slot in booking.slots.all():
                            slot.status = "BOOKED"
                            slot.locked_until = None
                            slot.locked_by = None
                            slot.save()

                        QRService.generate_qr_for_booking(booking)
                        try:
                            EmailNotificationService.send_booking_confirmation_email(booking)
                        except Exception as e:
                            pass
                    elif payment.customer:
                        # Wallet top-up
                        customer_profile = getattr(payment.customer, "customer_profile", None)
                        if customer_profile:
                            customer_profile.wallet_balance += amount
                            customer_profile.save()

            elif event == "payment.failed":
                payment_entity = payload.get("payload", {}).get("payment", {}).get("entity", {})
                rzp_order_id = payment_entity.get("order_id")
                payment = Payment.objects.select_for_update().filter(provider_order_id=rzp_order_id).first()
                if payment and payment.status == "PENDING":
                    payment.status = "FAILED"
                    payment.failure_reason = payment_entity.get("error_description", "Payment failed via gateway")
                    payment.save()

            elif event == "refund.processed":
                refund_entity = payload.get("payload", {}).get("refund", {}).get("entity", {})
                rzp_refund_id = refund_entity.get("id")
                refund = Refund.objects.filter(provider_refund_id=rzp_refund_id).first()
                if refund and refund.status != "COMPLETED":
                    refund.status = "COMPLETED"
                    refund.completed_at = timezone.now()
                    refund.save()

            # Record idempotent audit entry
            AuditLog.objects.create(
                action="WEBHOOK_PROCESSED",
                resource_type="WEBHOOK",
                resource_id=event_id,
                details={"event": event},
            )

        return Response({"status": "processed"}, status=status.HTTP_200_OK)


class PaymentListCreateView(views.APIView):
    """
    Lists payments with global search, rich filters, and pagination.
    """
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        now = timezone.now()
        today = now.date()

        if request.user.role in ("STAFF", "ADMIN") or request.user.is_superuser:
            payments = Payment.objects.all().select_related("customer", "booking", "booking__turf")
        else:
            payments = Payment.objects.filter(customer=request.user).select_related("booking", "booking__turf")

        # 1. Search filter
        search_query = request.query_params.get("search", "").strip()
        if search_query:
            payments = payments.filter(
                models.Q(payment_id__icontains=search_query)
                | models.Q(booking__booking_id__icontains=search_query)
                | models.Q(customer__email__icontains=search_query)
                | models.Q(customer__full_name__icontains=search_query)
                | models.Q(customer__customer_profile__phone_number__icontains=search_query)
                | models.Q(provider_order_id__icontains=search_query)
                | models.Q(provider_payment_id__icontains=search_query)
                | models.Q(transaction_reference__icontains=search_query)
            )

        # 2. Date Quick Filter
        date_filter = request.query_params.get("date_filter")
        if date_filter == "today":
            payments = payments.filter(created_at__date=today)
        elif date_filter == "yesterday":
            payments = payments.filter(created_at__date=today - timedelta(days=1))
        elif date_filter == "this_week":
            start_of_week = today - timedelta(days=today.weekday())
            payments = payments.filter(created_at__date__gte=start_of_week)
        elif date_filter == "this_month":
            payments = payments.filter(created_at__year=today.year, created_at__month=today.month)

        # 3. Status filter
        status_param = request.query_params.get("status")
        if status_param:
            if status_param == "PAID":
                payments = payments.filter(status__in=["PAID", "SUCCESSFUL"])
            else:
                payments = payments.filter(status=status_param)

        # 4. Method / Origin filter
        method_param = request.query_params.get("payment_method")
        if method_param:
            payments = payments.filter(payment_method=method_param)

        origin_param = request.query_params.get("origin")
        if origin_param == "ONLINE":
            payments = payments.filter(provider="RAZORPAY")
        elif origin_param == "OFFLINE":
            payments = payments.filter(provider__in=["CASH", "WALLET"])

        booking_id = request.query_params.get("booking_id")
        if booking_id:
            payments = payments.filter(booking__booking_id=booking_id)

        # Order by latest first
        payments = payments.order_by("-created_at")[:200]

        return Response(PaymentSerializer(payments, many=True).data)


class ProcessRefundView(views.APIView):
    """
    Initiates full or partial refunds:
    - Strictly gated by CanProcessRefunds permission (Manager/Admin only)
    - Executes real Razorpay API refund if paid via gateway
    - Credits customer wallet if refund_to == 'WALLET'
    - Records cash payout if refund_to == 'CASH'
    - Updates Payment status to REFUNDED or PARTIALLY_REFUNDED
    - Keeps Booking status and Payment status decoupled
    """
    permission_classes = [CanProcessRefunds]

    @transaction.atomic
    def post(self, request, pk):
        payment = get_object_or_404(Payment.objects.select_for_update(), pk=pk)
        amount_input = request.data.get("amount")
        refund_to = request.data.get("refund_to", "ORIGINAL").upper()
        reason = request.data.get("reason", "Customer requested cancellation / refund")

        if payment.status in ("REFUNDED", "FAILED", "PENDING"):
            return Response(
                {"error": f"Cannot refund payment in {payment.status} status."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Calculate already refunded sum for this payment
        existing_refunded = (
            payment.refunds.filter(status="COMPLETED").aggregate(models.Sum("amount"))["amount__sum"]
            or Decimal("0.00")
        )
        remaining_refundable = payment.amount - existing_refunded

        if remaining_refundable <= Decimal("0.00"):
            return Response(
                {"error": "This payment has already been fully refunded."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        refund_amount = Decimal(str(amount_input)) if amount_input else remaining_refundable
        if refund_amount > remaining_refundable:
            return Response(
                {
                    "error": f"Refund amount ₹{refund_amount} exceeds remaining refundable balance ₹{remaining_refundable}."
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        if refund_amount <= Decimal("0.00"):
            return Response(
                {"error": "Refund amount must be greater than zero."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        provider_refund_id = ""
        # If paid via Razorpay and refund to original source requested, execute real gateway refund
        if refund_to == "ORIGINAL" and payment.provider == "RAZORPAY" and payment.provider_payment_id:
            rzp_res = RazorpayService.initiate_refund(
                razorpay_payment_id=payment.provider_payment_id,
                amount_in_rupees=refund_amount,
                notes={"reason": reason, "booking_id": payment.booking.booking_id},
            )
            if rzp_res.get("success"):
                provider_refund_id = rzp_res.get("refund_id", "")
            else:
                return Response(
                    {"error": f"Razorpay Gateway refund failed: {rzp_res.get('error')}"},
                    status=status.HTTP_502_BAD_GATEWAY,
                )

        refund_id = f"REF-{uuid.uuid4().hex[:8].upper()}"
        refund = Refund.objects.create(
            refund_id=refund_id,
            payment=payment,
            booking=payment.booking,
            provider_refund_id=provider_refund_id,
            amount=refund_amount,
            refund_type="FULL" if refund_amount >= remaining_refundable else "PARTIAL",
            refund_to=refund_to,
            status="COMPLETED",
            reason=reason,
            reference_id=f"RREF-{uuid.uuid4().hex[:10].upper()}",
            initiated_by=request.user,
            completed_at=timezone.now(),
        )

        # If refund to wallet, credit customer wallet immediately
        if refund_to == "WALLET" and hasattr(payment.customer, "customer_profile"):
            prof = payment.customer.customer_profile
            prof.wallet_balance += refund_amount
            prof.save()

            WalletTransaction.objects.create(
                customer=payment.customer,
                amount=refund_amount,
                transaction_type="CREDIT",
                source="REFUND",
                reference_id=refund_id,
                description=f"Refund for payment {payment.payment_id}",
                balance_after=prof.wallet_balance,
            )

        # Update payment status
        total_now_refunded = existing_refunded + refund_amount
        if total_now_refunded >= payment.amount:
            payment.status = "REFUNDED"
        else:
            payment.status = "PARTIALLY_REFUNDED"
        payment.save()

        Notification.objects.create(
            user=payment.customer,
            notification_type="REFUND_PROCESSED",
            title=f"Refund Processed (₹{refund_amount})",
            message=f"Refund of ₹{refund_amount} for booking {payment.booking.booking_id} processed to {refund_to}.",
            data={"refund_id": refund_id},
        )

        AuditLog.objects.create(
            user=request.user,
            action="REFUND_PROCESSED",
            resource_type="PAYMENT",
            resource_id=payment.payment_id,
            details={
                "refund_id": refund_id,
                "amount": float(refund_amount),
                "refund_to": refund_to,
                "provider_refund_id": provider_refund_id,
            },
        )

        return Response(RefundSerializer(refund).data, status=status.HTTP_201_CREATED)


class RefundListView(views.APIView):
    def get_permissions(self):
        if self.request.method == "POST":
            return [CanProcessRefunds()]
        return [IsStaffOrAdmin()]

    def get(self, request):
        refunds = Refund.objects.all().select_related("payment", "booking", "booking__customer").order_by("-created_at")[:100]
        return Response(RefundSerializer(refunds, many=True).data)

    def post(self, request):
        booking_id = request.data.get("booking_id")
        payment_id = request.data.get("payment_id")

        payment = None
        if payment_id:
            payment = Payment.objects.filter(payment_id=payment_id).first()
        elif booking_id:
            if str(booking_id).isdigit():
                payment = Payment.objects.filter(booking_id=int(booking_id), status__in=["PAID", "SUCCESSFUL"]).order_by("-created_at").first()
            else:
                payment = Payment.objects.filter(booking__booking_id=booking_id, status__in=["PAID", "SUCCESSFUL"]).order_by("-created_at").first()

        if not payment:
            return Response(
                {"error": "No eligible paid transaction found to refund for this booking."},
                status=status.HTTP_404_NOT_FOUND,
            )

        # Delegate to ProcessRefundView logic
        return ProcessRefundView().post(request, pk=payment.pk)



class ManualCollectPaymentView(views.APIView):
    """
    Staff / Admin manual offline payment collection (Cash, Spot UPI, Card, Bank Transfer):
    - Fast under-10-seconds operation
    - Updates booking amount_paid and balance_due
    - Creates Payment record with status PAID
    - Confirms booking if paid and generates Match Pass
    - Audits payment entry with staff actor
    """
    permission_classes = [IsStaffOrAdmin]

    @transaction.atomic
    def post(self, request):
        booking_id = request.data.get("booking_id")
        amount_input = request.data.get("amount")
        payment_method = request.data.get("payment_method", "CASH").upper()
        transaction_ref = request.data.get("transaction_reference", "").strip()
        notes = request.data.get("notes", "").strip()

        if not booking_id or not amount_input:
            return Response(
                {"error": "booking_id and amount are required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Find booking by ID or booking_id
        if str(booking_id).isdigit():
            booking = get_object_or_404(Booking.objects.select_for_update(), pk=int(booking_id))
        else:
            booking = get_object_or_404(Booking.objects.select_for_update(), booking_id=booking_id)

        amount = Decimal(str(amount_input))
        if amount <= Decimal("0.00"):
            return Response(
                {"error": "Payment amount must be greater than zero."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        payment_id = Payment.generate_payment_id()
        txn_ref = transaction_ref or f"OFFLINE-{uuid.uuid4().hex[:10].upper()}"

        payment_type = "FULL" if booking.amount_paid == 0 and amount >= booking.final_amount else "PARTIAL" if booking.amount_paid == 0 else "BALANCE"

        payment = Payment.objects.create(
            payment_id=payment_id,
            booking=booking,
            customer=booking.customer,
            provider="CASH" if payment_method == "CASH" else "WALLET" if payment_method == "WALLET" else "CASH",
            amount=amount,
            currency="INR",
            payment_method=payment_method if payment_method in ["CASH", "UPI", "CARD", "BANK_TRANSFER", "OTHER"] else "CASH",
            payment_type=payment_type,
            transaction_reference=txn_ref,
            status="PAID",
            paid_at=timezone.now(),
            completed_at=timezone.now(),
            collected_by=request.user,
            notes=notes,
            gateway_response={"collected_by": request.user.email, "notes": notes},
        )

        booking.amount_paid += amount
        booking.balance_due = max(Decimal("0.00"), booking.final_amount - booking.amount_paid)

        if booking.status in ["PAYMENT_PENDING", "PENDING"]:
            booking.status = "CONFIRMED"
        booking.save()

        # Mark all slots booked
        for slot in booking.slots.all():
            slot.status = "BOOKED"
            slot.locked_until = None
            slot.locked_by = None
            slot.save()

        # Generate QR match pass if eligible
        QRService.generate_qr_for_booking(booking)
        try:
            EmailNotificationService.send_booking_confirmation_email(booking)
        except Exception as e:
            pass

        AuditLog.objects.create(
            user=request.user,
            action="OFFLINE_PAYMENT_COLLECTED",
            resource_type="PAYMENT",
            resource_id=payment.payment_id,
            details={
                "booking_id": booking.booking_id,
                "amount": float(amount),
                "method": payment_method,
                "reference": txn_ref,
                "staff": request.user.email,
                "remaining_balance": float(booking.balance_due),
            },
        )

        publish_event(
            channel="operations",
            event_type="OPERATIONS_UPDATE",
            payload={
                "type": "BALANCE_PAID",
                "booking_id": booking.booking_id,
                "amount": float(amount),
                "balance_due": float(booking.balance_due),
            },
        )

        return Response(
            {
                "message": f"Successfully collected ₹{amount} via {payment_method}.",
                "payment": PaymentSerializer(payment).data,
                "booking": BookingSerializer(booking).data,
            },
            status=status.HTTP_201_CREATED,
        )


class AdminPaymentStatsView(views.APIView):
    """
    Finance Dashboard Summary:
    Answers: "What is happening with money today?" and "What needs attention?"
    """
    permission_classes = [IsStaffOrAdmin]

    def get(self, request):
        today = timezone.now().date()
        all_payments = Payment.objects.all()

        # Overall numbers
        total_volume = all_payments.filter(status__in=["PAID", "SUCCESSFUL"]).aggregate(models.Sum("amount"))["amount__sum"] or Decimal("0.00")
        gateway_total = all_payments.filter(status__in=["PAID", "SUCCESSFUL"], provider="RAZORPAY").aggregate(models.Sum("amount"))["amount__sum"] or Decimal("0.00")
        cash_total = all_payments.filter(status__in=["PAID", "SUCCESSFUL"], provider="CASH").aggregate(models.Sum("amount"))["amount__sum"] or Decimal("0.00")

        # Today's authoritative numbers
        today_payments = all_payments.filter(created_at__date=today, status__in=["PAID", "SUCCESSFUL"])
        today_total = today_payments.aggregate(models.Sum("amount"))["amount__sum"] or Decimal("0.00")
        today_count = today_payments.count()

        today_pending_count = all_payments.filter(created_at__date=today, status="PENDING").count()

        today_refunds = Refund.objects.filter(created_at__date=today, status="COMPLETED").aggregate(models.Sum("amount"))["amount__sum"] or Decimal("0.00")
        today_refunds_count = Refund.objects.filter(created_at__date=today, status="COMPLETED").count()

        today_offline = today_payments.filter(provider="CASH").aggregate(models.Sum("amount"))["amount__sum"] or Decimal("0.00")
        today_online = today_payments.filter(provider="RAZORPAY").aggregate(models.Sum("amount"))["amount__sum"] or Decimal("0.00")

        # Reconciliation / Attention count
        anomalies = ReconciliationEngine.scan_anomalies()
        reconciliation_count = len(anomalies)

        return Response({
            "today": {
                "revenue": float(today_total),
                "payments_count": today_count,
                "pending_count": today_pending_count,
                "refunds_amount": float(today_refunds),
                "refunds_count": today_refunds_count,
                "offline_amount": float(today_offline),
                "online_amount": float(today_online),
            },
            "needs_attention": {
                "pending_payments": today_pending_count,
                "refunds_pending": Refund.objects.filter(status="REQUESTED").count(),
                "reconciliation_issues": reconciliation_count,
            },
            "all_time": {
                "total_volume": float(total_volume),
                "gateway_total": float(gateway_total),
                "cash_total": float(cash_total),
                "total_transactions": all_payments.count(),
            }
        })


class ReceiptDetailView(views.APIView):
    """
    Returns official branded human-friendly digital receipt.
    Works by payment_id or booking_id.
    """
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, identifier):
        # 1. Check if identifier is payment_id
        payment = Payment.objects.filter(payment_id=identifier).select_related("booking", "customer").first()
        if payment:
            # Check ownership
            if request.user.role == "CUSTOMER" and payment.customer != request.user:
                return Response({"error": "Unauthorized access."}, status=status.HTTP_403_FORBIDDEN)
            receipt = ReceiptGenerator.generate_receipt_for_payment(payment)
            return Response(receipt)

        # 2. Check if identifier is booking_id
        booking = Booking.objects.filter(booking_id=identifier).select_related("turf", "customer").first()
        if booking:
            if request.user.role == "CUSTOMER" and booking.customer != request.user:
                return Response({"error": "Unauthorized access."}, status=status.HTTP_403_FORBIDDEN)
            receipt = ReceiptGenerator.generate_receipt_for_booking(booking)
            return Response(receipt)

        return Response({"error": "Receipt not found for this identifier."}, status=status.HTTP_404_NOT_FOUND)


class CancellationQuoteView(views.APIView):
    """
    Calculates cancellation fee and refund quote according to policy.
    No manual arithmetic.
    """
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, booking_id):
        booking = get_object_or_404(Booking, booking_id=booking_id)
        if request.user.role == "CUSTOMER" and booking.customer != request.user:
            return Response({"error": "Unauthorized access."}, status=status.HTTP_403_FORBIDDEN)

        quote = CancellationPolicyEngine.calculate_refund(booking)
        return Response(quote)


class DailyCashDrawerView(views.APIView):
    """
    Daily Cash Drawer Management:
    - Get summary of today's opening, offline cash, refunds, expected closing, actual closing.
    - Set opening cash or close drawer with actual cash count.
    """
    permission_classes = [IsStaffOrAdmin]

    def get(self, request):
        today = timezone.now().date()
        drawer, _ = DailyCashDrawer.objects.get_or_create(date=today)
        return Response(drawer.get_summary())

    def post(self, request):
        today = timezone.now().date()
        action = request.data.get("action")  # 'SET_OPENING' or 'CLOSE_DRAWER'

        drawer, _ = DailyCashDrawer.objects.get_or_create(date=today)

        if action == "SET_OPENING":
            opening = request.data.get("opening_cash", 0)
            drawer.opening_cash = Decimal(str(opening))
            drawer.save()
            AuditLog.objects.create(
                user=request.user,
                action="DAILY_CASH_OPENING_SET",
                resource_type="CASH_DRAWER",
                resource_id=str(drawer.date),
                details={"opening_cash": float(drawer.opening_cash)},
            )
            return Response(drawer.get_summary())

        elif action == "CLOSE_DRAWER":
            actual_cash = request.data.get("actual_closing_cash")
            notes = request.data.get("notes", "")

            if actual_cash is None:
                return Response({"error": "actual_closing_cash is required."}, status=status.HTTP_400_BAD_REQUEST)

            drawer.actual_closing_cash = Decimal(str(actual_cash))
            drawer.closed_by = request.user
            drawer.closed_at = timezone.now()
            drawer.notes = notes

            summary = drawer.get_summary()
            if summary["has_discrepancy"]:
                drawer.status = "DISCREPANCY"
            else:
                drawer.status = "CLOSED"
            drawer.save()

            AuditLog.objects.create(
                user=request.user,
                action="DAILY_CASH_DRAWER_CLOSED",
                resource_type="CASH_DRAWER",
                resource_id=str(drawer.date),
                details={
                    "actual_closing": float(drawer.actual_closing_cash),
                    "variance": summary["variance"],
                    "status": drawer.status,
                },
            )
            return Response(drawer.get_summary())

        return Response({"error": "Invalid action. Use SET_OPENING or CLOSE_DRAWER."}, status=status.HTTP_400_BAD_REQUEST)


class ReconciliationScanView(views.APIView):
    """
    Scans for payment/booking anomalies and returns human-readable review cards.
    """
    permission_classes = [IsManager]

    def get(self, request):
        anomalies = ReconciliationEngine.scan_anomalies()
        return Response({"count": len(anomalies), "anomalies": anomalies})


class ReconciliationResolveView(views.APIView):
    """
    Resolves a flagged reconciliation anomaly inside an atomic transaction.
    """
    permission_classes = [IsManager]

    def post(self, request):
        anomaly_type = request.data.get("anomaly_type")
        payment_id = request.data.get("payment_id")
        booking_id = request.data.get("booking_id")

        if not anomaly_type:
            return Response({"error": "anomaly_type is required."}, status=status.HTTP_400_BAD_REQUEST)

        try:
            res = ReconciliationEngine.resolve_anomaly(
                anomaly_type=anomaly_type,
                payment_id=payment_id,
                booking_id=booking_id,
                user=request.user,
            )
            return Response(res, status=status.HTTP_200_OK)
        except Exception as e:
            return Response({"error": str(e)}, status=status.HTTP_400_BAD_REQUEST)


class ExportPaymentsCsvView(views.APIView):
    """
    Exports payment transaction records to a CSV file.
    """
    permission_classes = [IsStaffOrAdmin]

    def get(self, request):
        response = HttpResponse(content_type="text/csv")
        filename = f"friends_turf_payments_{timezone.now().strftime('%Y%m%d_%H%M%S')}.csv"
        response["Content-Disposition"] = f'attachment; filename="{filename}"'

        writer = csv.writer(response)
        writer.writerow([
            "Payment ID",
            "Booking Reference",
            "Customer Email",
            "Amount (INR)",
            "Provider",
            "Payment Method",
            "Payment Type",
            "Status",
            "Transaction Reference",
            "Provider Order ID",
            "Provider Payment ID",
            "Paid At",
            "Created At",
        ])

        payments = Payment.objects.all().select_related("booking", "customer").order_by("-created_at")
        for p in payments:
            writer.writerow([
                p.payment_id,
                p.booking.booking_id if p.booking else "N/A",
                p.customer.email if p.customer else "N/A",
                float(p.amount),
                p.provider,
                p.payment_method,
                p.payment_type,
                p.status,
                p.transaction_reference,
                p.provider_order_id,
                p.provider_payment_id,
                p.paid_at.isoformat() if p.paid_at else "",
                p.created_at.isoformat() if p.created_at else "",
            ])

        return response
