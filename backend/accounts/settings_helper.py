"""
Business Settings Helper Service for Friends Turf.
Centralized, authoritative, and cached provider for dynamic system configurations.
"""
from decimal import Decimal
from typing import Dict, Any

DEFAULT_BUSINESS_SETTINGS = {
    "company": {
        "name": "Friends Turf",
        "tagline": "PLAY HARD. BOOK DIRECT. OWN THE PITCH.",
        "address": "Near Sirupooluvapatti, Kamatchepuram, Tiruppur, Tamil Nadu 641603 (RTO Office Backside)",
        "phone": "+91 93619 89494",
        "email": "contact@friendsturf.com",
        "support_email": "support@friendsturf.com",
        "website": "https://friendsturf.com",
        "instagram": "@friendsturf_tiruppur",
        "whatsapp": "+91 93639 89494",
        "timezone": "Asia/Kolkata",
        "currency": "INR",
        "gstin": "33ABCDE1234F1Z5",
        "logo_url": "/logo.png",
    },
    "booking": {
        "advanceBookingDays": 14,
        "minDurationMinutes": 60,
        "maxDurationMinutes": 180,
        "slotHoldMinutes": 5,
        "cancellationFullRefundHours": 24,
        "cancellationPartialRefundHours": 6,
        "partialRefundPercent": 50,
        "cancellationFeeMidTierPercent": 20,
        "allowRescheduling": True,
        "rescheduleCutoffHours": 6,
    },
    "hours": {
        "openTime": "06:00",
        "closeTime": "23:00",
        "slotDurationMinutes": 60,
        "bufferTimeMinutes": 0,
        "allowMidnightBookings": False,
    },
    "payments": {
        "gateway": "RAZORPAY",
        "mode": "TEST",
        "upiId": "friendsturf@okhdfcbank",
        "enableSplitDeposit": True,
        "advanceDepositPercent": 50,
        "taxPercentage": 18.0,
        "isTaxIncluded": True,
        "minSlotPrice": 1.0,
        "minTopUpAmount": 10.0,
    },
    "checkin": {
        "windowOpenMinutes": 30,
        "gracePeriodMinutes": 30,
        "allowManualOverride": True,
        "requireOverrideReason": True,
    },
    "notifications": {
        "sendConfirmationImmediately": True,
        "reminder24h": True,
        "reminder2h": True,
        "postMatchFeedbackHours": 2,
    },
}


class BusinessSettingsHelper:
    """
    Singleton-style utility for fetching database-backed dynamic business settings
    with robust fallback defaults.
    """

    @classmethod
    def get_section(cls, section_name: str) -> Dict[str, Any]:
        from .models import BusinessSetting

        defaults = DEFAULT_BUSINESS_SETTINGS.get(section_name, {})
        try:
            record = BusinessSetting.objects.filter(key=section_name).first()
            if record and isinstance(record.value, dict):
                return {**defaults, **record.value}
        except Exception:
            pass
        return defaults

    @classmethod
    def get_company_settings(cls) -> Dict[str, Any]:
        return cls.get_section("company")

    @classmethod
    def get_booking_rules(cls) -> Dict[str, Any]:
        return cls.get_section("booking")

    @classmethod
    def get_operating_hours(cls) -> Dict[str, Any]:
        return cls.get_section("hours")

    @classmethod
    def get_payment_settings(cls) -> Dict[str, Any]:
        return cls.get_section("payments")

    @classmethod
    def get_checkin_settings(cls) -> Dict[str, Any]:
        return cls.get_section("checkin")

    @classmethod
    def get_notification_settings(cls) -> Dict[str, Any]:
        return cls.get_section("notifications")

    # Convenience helper getters
    @classmethod
    def get_tax_rate_percentage(cls) -> Decimal:
        payments = cls.get_payment_settings()
        val = payments.get("taxPercentage", 18.0)
        return Decimal(str(val))

    @classmethod
    def get_slot_lock_duration_minutes(cls) -> int:
        booking = cls.get_booking_rules()
        return int(booking.get("slotHoldMinutes", 5))

    @classmethod
    def get_advance_deposit_fraction(cls) -> Decimal:
        payments = cls.get_payment_settings()
        pct = payments.get("advanceDepositPercent", 50)
        return Decimal(str(pct)) / Decimal("100.00")

    @classmethod
    def get_checkin_open_minutes(cls) -> int:
        checkin = cls.get_checkin_settings()
        return int(checkin.get("windowOpenMinutes", 30))

    @classmethod
    def get_checkin_grace_minutes(cls) -> int:
        checkin = cls.get_checkin_settings()
        return int(checkin.get("gracePeriodMinutes", 30))
