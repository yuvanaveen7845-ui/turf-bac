from django.urls import path
from .views import QRValidateScanView, GetBookingQRView, CheckInHistoryView

urlpatterns = [
    path("scan/", QRValidateScanView.as_view(), name="qr_scan"),
    path("logs/", CheckInHistoryView.as_view(), name="checkin_logs"),
    path("<str:booking_id>/", GetBookingQRView.as_view(), name="get_booking_qr"),
]
