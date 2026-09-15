from django.db import models
from django.conf import settings


class WalletTransaction(models.Model):
    TYPE_CHOICES = (
        ("CREDIT", "Credit (+)"),
        ("DEBIT", "Debit (-)"),
    )

    SOURCE_CHOICES = (
        ("REFUND", "Booking Refund"),
        ("PROMOTIONAL", "Promotional Bonus"),
        ("REFERRAL", "Referral Reward"),
        ("ADMIN", "Admin Adjustment"),
        ("REDEMPTION", "Booking Payment"),
        ("TOP_UP", "Direct Wallet Top-up"),
    )

    customer = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="wallet_transactions",
    )
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    transaction_type = models.CharField(max_length=10, choices=TYPE_CHOICES)
    source = models.CharField(max_length=20, choices=SOURCE_CHOICES)
    reference_id = models.CharField(max_length=50, blank=True)
    description = models.CharField(max_length=255)
    balance_after = models.DecimalField(max_digits=10, decimal_places=2)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        sign = "+" if self.transaction_type == "CREDIT" else "-"
        return f"{self.customer.email} | {sign}₹{self.amount} ({self.source})"


class LoyaltyTransaction(models.Model):
    TYPE_CHOICES = (
        ("EARN", "Earned (+)"),
        ("REDEEM", "Redeemed (-)"),
        ("BONUS", "Bonus Points (+)"),
        ("EXPIRE", "Expired (-)"),
    )

    SOURCE_CHOICES = (
        ("BOOKING", "Completed Turf Booking"),
        ("PROMOTION", "Seasonal Promotion"),
        ("REFERRAL", "Referral Completed"),
        ("ADMIN", "Manual Admin Reward"),
    )

    customer = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="loyalty_transactions",
    )
    points = models.IntegerField()
    transaction_type = models.CharField(max_length=10, choices=TYPE_CHOICES)
    source = models.CharField(max_length=20, choices=SOURCE_CHOICES)
    reference_id = models.CharField(max_length=50, blank=True)
    description = models.CharField(max_length=255)
    balance_after = models.IntegerField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        sign = "+" if self.transaction_type in ("EARN", "BONUS") else "-"
        return f"{self.customer.email} | {sign}{self.points} pts ({self.source})"
