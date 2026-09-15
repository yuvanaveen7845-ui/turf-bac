from django.urls import path
from .views import (
    CouponListCreateView,
    CouponDetailView,
    ValidateCouponView,
    ReferralInfoView,
)

urlpatterns = [
    path("coupons/", CouponListCreateView.as_view(), name="coupon_list"),
    path("coupons/<str:pk>/", CouponDetailView.as_view(), name="coupon_detail"),
    path("coupons/validate/", ValidateCouponView.as_view(), name="validate_coupon"),
    path("referrals/", ReferralInfoView.as_view(), name="referral_info"),
]
