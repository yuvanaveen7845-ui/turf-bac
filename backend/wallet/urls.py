from django.urls import path
from .views import (
    WalletDetailView,
    WalletTopUpView,
    WalletCreateRazorpayOrderView,
    WalletVerifyRazorpayPaymentView,
    AdminAdjustWalletView,
)

urlpatterns = [
    path("balance/", WalletDetailView.as_view(), name="wallet_balance"),
    path("top-up/", WalletTopUpView.as_view(), name="wallet_topup"),
    path("razorpay/create-order/", WalletCreateRazorpayOrderView.as_view(), name="wallet_razorpay_create_order"),
    path("razorpay/verify/", WalletVerifyRazorpayPaymentView.as_view(), name="wallet_razorpay_verify"),
    path("admin/adjust/", AdminAdjustWalletView.as_view(), name="admin_adjust_wallet"),
]
