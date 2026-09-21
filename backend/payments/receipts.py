import uuid
from datetime import datetime
from decimal import Decimal
from django.utils import timezone
from .models import Payment
from bookings.models import Booking
from accounts.settings_helper import BusinessSettingsHelper


class ReceiptGenerator:
    """
    Receipt & Invoice Generator for Friends Turf.
    Generates human-friendly, official branded receipts containing:
    - Business Details (Name, Address, GSTIN, Support Email/Phone) dynamically from BusinessSetting
    - Receipt ID (REC-26-XXXXX)
    - Booking Reference (FT-26-XXXXX)
    - Pitch & Timing Details
    - Financial & Tax Breakdown (Base, Adjustments, Discounts, GST, Amount Paid, Balance Due)
    - Payment Mode (Online Razorpay vs Offline Cash/UPI/Card)
    """

    @classmethod
    def get_business_info(cls):
        comp = BusinessSettingsHelper.get_company_settings()
        return {
            "company_name": comp.get("name", "Friends Turf Sports Arena"),
            "brand_name": comp.get("name", "Friends Turf"),
            "tagline": comp.get("tagline", "PLAY HARD. BOOK DIRECT. OWN THE PITCH."),
            "address": comp.get("address", "Near Sirupooluvapatti, Kamatchepuram, Tiruppur, Tamil Nadu 641603 (RTO Office Backside)"),
            "gstin": comp.get("gstin", "33ABCDE1234F1Z5"),
            "email": comp.get("support_email", comp.get("email", "support@friendsturf.com")),
            "phone": comp.get("phone", "+91 93619 89494"),
            "website": comp.get("website", "https://friendsturf.com"),
        }

    # Backward compatibility property
    BUSINESS_INFO = {
        "company_name": "Friends Turf Sports Arena",
        "brand_name": "Friends Turf",
        "tagline": "PLAY HARD. BOOK DIRECT. OWN THE PITCH.",
        "address": "Near Sirupooluvapatti, Kamatchepuram, Tiruppur, Tamil Nadu 641603 (RTO Office Backside)",
        "gstin": "33ABCDE1234F1Z5",
        "email": "support@friendsturf.com",
        "phone": "+91 93619 89494",
        "website": "https://friendsturf.com",
    }

    @classmethod
    def generate_receipt_for_payment(cls, payment):
        """
        Generates structured receipt data for a given Payment instance.
        """
        booking = payment.booking
        customer = payment.customer

        year_suffix = payment.created_at.strftime("%y") if payment.created_at else timezone.now().strftime("%y")
        receipt_no = f"REC-{year_suffix}-{payment.payment_id.replace('PAY-', '')[:6]}"

        breakdown = booking.pricing_breakdown or {}
        subtotal = float(booking.total_amount)
        discount = float(booking.discount_amount)
        tax = float(booking.tax_amount)
        final_total = float(booking.final_amount)
        paid = float(booking.amount_paid)
        balance = float(booking.balance_due)

        # Slot times
        slots_info = [
            f"{s.start_time.strftime('%H:%M')} - {s.end_time.strftime('%H:%M')}"
            for s in booking.slots.all()
        ]
        slot_summary = ", ".join(slots_info) if slots_info else f"{booking.start_time.strftime('%H:%M')} - {booking.end_time.strftime('%H:%M')}"

        is_online = payment.provider == "RAZORPAY"
        payment_origin = "Online · Razorpay" if is_online else f"Offline · {payment.payment_method}"

        return {
            "receipt_number": receipt_no,
            "issued_at": (payment.paid_at or payment.created_at or timezone.now()).isoformat(),
            "business": cls.get_business_info(),
            "customer": {
                "name": customer.full_name or customer.first_name or customer.email.split("@")[0],
                "email": customer.email,
                "phone": getattr(customer, "phone", "") or getattr(customer.customer_profile, "phone_number", "") if hasattr(customer, "customer_profile") else "",
            },
            "booking": {
                "booking_id": booking.booking_id,
                "turf_name": booking.turf.name,
                "sport_type": booking.turf.sport_type,
                "location": booking.turf.location,
                "match_date": booking.date.strftime("%d %b %Y"),
                "slot_timings": slot_summary,
                "status": booking.status,
            },
            "payment": {
                "payment_id": payment.payment_id,
                "amount_paid_in_this_transaction": float(payment.amount),
                "payment_method": payment.payment_method,
                "provider": payment.provider,
                "payment_origin": payment_origin,
                "transaction_reference": payment.transaction_reference,
                "status": payment.status,
                "paid_at": (payment.paid_at or payment.created_at).isoformat() if payment.paid_at or payment.created_at else None,
            },
            "financial_summary": {
                "base_subtotal": subtotal,
                "membership_discount": float(breakdown.get("membership_discount", 0.0)),
                "coupon_discount": float(breakdown.get("coupon_discount", 0.0)),
                "coupon_code": booking.coupon_code or None,
                "total_discount": discount,
                "taxable_amount": float(breakdown.get("taxable_amount", subtotal - discount)),
                "gst_percentage": float(breakdown.get("tax_rate_percent", 18.0)),
                "gst_amount": tax,
                "cgst_amount": round(tax / 2.0, 2),
                "sgst_amount": round(tax / 2.0, 2),
                "final_amount": final_total,
                "total_paid_to_date": paid,
                "balance_due": balance,
                "currency": "INR",
            },
            "qr_pass_eligible": booking.status in ("CONFIRMED", "CHECKED_IN", "IN_PROGRESS"),
        }

    @classmethod
    def generate_receipt_for_booking(cls, booking):
        """
        Generates receipt data for a booking, using the latest successful payment or booking details.
        """
        latest_payment = (
            Payment.objects.filter(booking=booking, status__in=["PAID", "SUCCESSFUL"])
            .order_by("-created_at")
            .first()
        )
        if latest_payment:
            return cls.generate_receipt_for_payment(latest_payment)

        # Fallback if no payment record exists yet
        customer = booking.customer
        year_suffix = booking.date.strftime("%y") if booking.date else timezone.now().strftime("%y")
        receipt_no = f"REC-{year_suffix}-{booking.booking_id.replace('FT-', '')[:6]}"
        breakdown = booking.pricing_breakdown or {}
        subtotal = float(booking.total_amount)
        discount = float(booking.discount_amount)
        tax = float(booking.tax_amount)
        final_total = float(booking.final_amount)

        return {
            "receipt_number": receipt_no,
            "issued_at": (booking.created_at or timezone.now()).isoformat(),
            "business": cls.BUSINESS_INFO,
            "customer": {
                "name": customer.full_name or customer.first_name or customer.email.split("@")[0],
                "email": customer.email,
                "phone": getattr(customer, "phone", "") or "",
            },
            "booking": {
                "booking_id": booking.booking_id,
                "turf_name": booking.turf.name,
                "sport_type": booking.turf.sport_type,
                "location": booking.turf.location,
                "match_date": booking.date.strftime("%d %b %Y"),
                "slot_timings": f"{booking.start_time.strftime('%H:%M')} - {booking.end_time.strftime('%H:%M')}",
                "status": booking.status,
            },
            "payment": {
                "payment_id": "PENDING",
                "amount_paid_in_this_transaction": float(booking.amount_paid),
                "payment_method": "PENDING",
                "provider": "PENDING",
                "payment_origin": "Pending Payment",
                "transaction_reference": "N/A",
                "status": "PENDING",
                "paid_at": None,
            },
            "financial_summary": {
                "base_subtotal": subtotal,
                "membership_discount": float(breakdown.get("membership_discount", 0.0)),
                "coupon_discount": float(breakdown.get("coupon_discount", 0.0)),
                "coupon_code": booking.coupon_code or None,
                "total_discount": discount,
                "taxable_amount": float(breakdown.get("taxable_amount", subtotal - discount)),
                "gst_percentage": float(breakdown.get("tax_rate_percent", 18.0)),
                "gst_amount": tax,
                "cgst_amount": round(tax / 2.0, 2),
                "sgst_amount": round(tax / 2.0, 2),
                "final_amount": final_total,
                "total_paid_to_date": float(booking.amount_paid),
                "balance_due": float(booking.balance_due),
                "currency": "INR",
            },
            "qr_pass_eligible": booking.status in ("CONFIRMED", "CHECKED_IN", "IN_PROGRESS"),
        }
