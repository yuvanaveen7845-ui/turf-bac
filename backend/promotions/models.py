from django.db import models
from django.conf import settings
from django.utils import timezone
from turfs.models import Turf
from bookings.models import Booking


class Coupon(models.Model):
    DISCOUNT_TYPE_CHOICES = (
        ("PERCENTAGE", "Percentage Discount (%)"),
        ("FIXED", "Fixed Cash Discount (₹)"),
    )

    COUPON_TYPE_CHOICES = (
        ("GENERAL", "General Promotional"),
        ("FIRST_BOOKING", "First Booking Discount"),
        ("REFERRAL", "Referral Reward"),
        ("BIRTHDAY", "Birthday Special"),
        ("FESTIVAL", "Festival Offer"),
        ("OFF_PEAK", "Off-Peak Hours Deal"),
        ("WEEKEND", "Weekend Special"),
        ("MEMBERSHIP", "Exclusive Member Discount"),
    )

    code = models.CharField(max_length=30, unique=True, db_index=True)
    title = models.CharField(max_length=150)
    description = models.TextField(blank=True)
    discount_type = models.CharField(
        max_length=20, choices=DISCOUNT_TYPE_CHOICES, default="PERCENTAGE"
    )
    discount_value = models.DecimalField(max_digits=8, decimal_places=2)
    min_booking_amount = models.DecimalField(
        max_digits=10, decimal_places=2, default=0.00
    )
    max_discount_amount = models.DecimalField(
        max_digits=10, decimal_places=2, null=True, blank=True
    )

    start_date = models.DateField(default=timezone.now)
    end_date = models.DateField()
    usage_limit = models.IntegerField(default=100)
    per_user_limit = models.IntegerField(default=1)
    usage_count = models.IntegerField(default=0)

    applicable_turfs = models.ManyToManyField(Turf, blank=True, related_name="coupons")
    coupon_type = models.CharField(
        max_length=30, choices=COUPON_TYPE_CHOICES, default="GENERAL"
    )
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.code} - {self.discount_value}{'%' if self.discount_type == 'PERCENTAGE' else '₹'}"

    def is_valid_for_user(self, user, booking_amount):
        now = timezone.now().date()
        if not self.is_active:
            return False, "Coupon is not active"
        if now < self.start_date or now > self.end_date:
            return False, "Coupon has expired or is not yet active"
        if self.usage_count >= self.usage_limit:
            return False, "Coupon usage limit reached"
        if booking_amount < self.min_booking_amount:
            return (
                False,
                f"Minimum booking amount of ₹{self.min_booking_amount} required",
            )

        user_usages = CouponUsage.objects.filter(coupon=self, user=user).count()
        if user_usages >= self.per_user_limit:
            return False, "You have already reached the usage limit for this coupon"

        return True, "Valid"

    def calculate_discount(self, amount):
        if self.discount_type == "PERCENTAGE":
            discount = (amount * self.discount_value) / 100
            if self.max_discount_amount:
                discount = min(discount, self.max_discount_amount)
            return round(discount, 2)
        else:
            return min(self.discount_value, amount)


class CouponUsage(models.Model):
    coupon = models.ForeignKey(Coupon, on_delete=models.CASCADE, related_name="usages")
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="coupon_usages"
    )
    booking = models.ForeignKey(
        Booking, on_delete=models.CASCADE, related_name="coupon_usages"
    )
    discount_applied = models.DecimalField(max_digits=10, decimal_places=2)
    used_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.user.email} used {self.coupon.code} on {self.booking.booking_id}"


class ReferralReward(models.Model):
    referrer = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="rewards_earned",
    )
    referred_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="referral_bonuses",
    )
    booking = models.ForeignKey(
        Booking, null=True, blank=True, on_delete=models.SET_NULL
    )
    reward_amount = models.DecimalField(max_digits=8, decimal_places=2, default=100.00)
    status = models.CharField(
        max_length=20,
        choices=(("PENDING", "Pending"), ("CREDITED", "Credited")),
        default="PENDING",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    credited_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return f"Referral: {self.referrer.email} invited {self.referred_user.email} [₹{self.reward_amount}]"
