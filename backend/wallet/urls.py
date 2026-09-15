from django.urls import path
from .views import (
    WalletDetailView,
    WalletTopUpView,
    LoyaltyDetailView,
    LoyaltyRedeemView,
    AdminAdjustWalletView,
)

urlpatterns = [
    path("balance/", WalletDetailView.as_view(), name="wallet_balance"),
    path("top-up/", WalletTopUpView.as_view(), name="wallet_topup"),
    path("loyalty/", LoyaltyDetailView.as_view(), name="loyalty_detail"),
    path("loyalty/redeem/", LoyaltyRedeemView.as_view(), name="loyalty_redeem"),
    path("admin/adjust/", AdminAdjustWalletView.as_view(), name="admin_adjust_wallet"),
]
