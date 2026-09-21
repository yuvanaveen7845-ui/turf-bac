from django.urls import path
from .views import (
    QRValidateScanView,
    QRManualOverrideView,
    QRCollectBalanceAndAdmitView,
    GetBookingPassView,
    CheckInLogsView,
    CheckInAnalyticsView,
    AdminRevokePassView,
    AdminRegeneratePassView,
    QRImageServeView,
)

urlpatterns = [
    path("scan/", QRValidateScanView.as_view(), name="qr_scan"),
    path("override/", QRManualOverrideView.as_view(), name="qr_override"),
    path("collect-balance-and-admit/", QRCollectBalanceAndAdmitView.as_view(), name="qr_collect_balance_and_admit"),
    path("collect-balance-admit/", QRCollectBalanceAndAdmitView.as_view(), name="qr_collect_balance_admit"),
    path("image/<str:booking_id>/", QRImageServeView.as_view(), name="qr_image_serve"),
    path("pass/<str:booking_id>/", GetBookingPassView.as_view(), name="get_booking_pass"),
    path("logs/", CheckInLogsView.as_view(), name="checkin_logs"),
    path("analytics/", CheckInAnalyticsView.as_view(), name="checkin_analytics"),
    path("admin/revoke/", AdminRevokePassView.as_view(), name="admin_revoke_pass"),
    path("admin/regenerate/", AdminRegeneratePassView.as_view(), name="admin_regenerate_pass"),
    # Backward compatibility
    path("<str:booking_id>/", GetBookingPassView.as_view(), name="get_booking_qr_legacy"),
]
