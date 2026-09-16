from django.urls import path
from .views import (
    QRValidateScanView,
    QRManualOverrideView,
    GetBookingPassView,
    CheckInLogsView,
    CheckInAnalyticsView,
    AdminRevokePassView,
    AdminRegeneratePassView,
)

urlpatterns = [
    path("scan/", QRValidateScanView.as_view(), name="qr_scan"),
    path("override/", QRManualOverrideView.as_view(), name="qr_override"),
    path("pass/<str:booking_id>/", GetBookingPassView.as_view(), name="get_booking_pass"),
    path("logs/", CheckInLogsView.as_view(), name="checkin_logs"),
    path("analytics/", CheckInAnalyticsView.as_view(), name="checkin_analytics"),
    path("admin/revoke/", AdminRevokePassView.as_view(), name="admin_revoke_pass"),
    path("admin/regenerate/", AdminRegeneratePassView.as_view(), name="admin_regenerate_pass"),
    # Backward compatibility
    path("<str:booking_id>/", GetBookingPassView.as_view(), name="get_booking_qr_legacy"),
]
