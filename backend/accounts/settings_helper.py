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
        "facebook": "@friendsturf_tiruppur",
        "whatsapp": "+91 93639 89494",
        "timezone": "Asia/Kolkata",
        "currency": "INR",
        "gstin": "33ABCDE1234F1Z5",
        "logo_url": "/logo.png",
        "banner_title": "Friends Turf Sports Complex",
        "banner_landmark": "RTO Backside",
        "banner_image_url": "",
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
        "keyId": "",
        "keySecret": "",
        "callbackUrl": "",
        "upiId": "friendsturf@okhdfcbank",
        "enableSplitDeposit": True,
        "hourlyAdvanceRate": 100.0,
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
    "auth": {
        "google_client_id": "",
    },
    "features": {
        "RECURRING_BOOKINGS": True,
        "PARTIAL_PAYMENTS": True,
        "WALK_IN_BOOKINGS": True,
        "DYNAMIC_PRICING": True,
        "QR_CHECKIN": True,
        "ONLINE_PAYMENTS": True,
        "OFFLINE_PAYMENTS": True,
        "COUPONS": False,
        "REVIEWS": True,
        "ADVANCED_REPORTING": True,
    },
}


class BusinessSettingsHelper:
    """
    Singleton-style utility for fetching database-backed dynamic business settings
    with robust fallback defaults.
    """

    CACHE_TTL = 300  # 5 minutes

    @classmethod
    def get_section(cls, section_name: str) -> Dict[str, Any]:
        from django.core.cache import cache

        cache_key = f"biz_settings_{section_name}"
        cached = cache.get(cache_key)
        if cached is not None:
            return cached

        from .models import BusinessSetting

        defaults = DEFAULT_BUSINESS_SETTINGS.get(section_name, {})
        result = dict(defaults)
        try:
            record = BusinessSetting.objects.filter(key=section_name).first()
            if record and isinstance(record.value, dict):
                result = {**result, **record.value}
        except Exception:
            pass

        # Fallback dynamic retrieval for auth section (reads env/settings if not set in DB)
        if section_name == "auth" and not result.get("google_client_id"):
            from django.conf import settings
            import os
            result["google_client_id"] = getattr(settings, "GOOGLE_CLIENT_ID", "") or os.getenv("GOOGLE_CLIENT_ID", "")

        # Fallback dynamic retrieval for payments section
        if section_name == "payments":
            from django.conf import settings
            canonical_key = getattr(settings, "RAZORPAY_KEY_ID", "")
            if not result.get("keyId"):
                result["keyId"] = canonical_key
            # Legacy compatibility
            if not result.get("testKeyId"):
                result["testKeyId"] = canonical_key if not canonical_key.startswith("rzp_live_") else ""
            if not result.get("liveKeyId"):
                result["liveKeyId"] = canonical_key if canonical_key.startswith("rzp_live_") else ""
            if not result.get("callbackUrl"):
                result["callbackUrl"] = getattr(settings, "RAZORPAY_CALLBACK_URL", "")
            result["isDebug"] = getattr(settings, "DEBUG", True)
            result["activeMode"] = "LIVE" if canonical_key.startswith("rzp_live_") else ("TEST" if getattr(settings, "DEBUG", True) else "LIVE")

        cache.set(cache_key, result, timeout=cls.CACHE_TTL)
        return result

    @classmethod
    def invalidate_cache(cls, section_name: str = None):
        """Call after admin updates BusinessSetting to bust the cache."""
        from django.core.cache import cache

        if section_name:
            cache.delete(f"biz_settings_{section_name}")
        else:
            for key in DEFAULT_BUSINESS_SETTINGS:
                cache.delete(f"biz_settings_{key}")

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
    def get_features(cls) -> Dict[str, bool]:
        return cls.get_section("features")

    @classmethod
    def is_feature_enabled(cls, feature_name: str, default: bool = True) -> bool:
        features = cls.get_features()
        if not isinstance(features, dict):
            return default
        return bool(features.get(feature_name, default))

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
    def is_tax_included(cls) -> bool:
        payments = cls.get_payment_settings()
        return bool(payments.get("isTaxIncluded", True))

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
    def get_hourly_advance_rate(cls) -> Decimal:
        payments = cls.get_payment_settings()
        rate = payments.get("hourlyAdvanceRate", 100.0)
        return Decimal(str(rate))

    @classmethod
    def get_checkin_open_minutes(cls) -> int:
        checkin = cls.get_checkin_settings()
        return int(checkin.get("windowOpenMinutes", 30))

    @classmethod
    def get_checkin_grace_minutes(cls) -> int:
        checkin = cls.get_checkin_settings()
        return int(checkin.get("gracePeriodMinutes", 30))
