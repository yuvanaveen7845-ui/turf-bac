from django.urls import path
from .views import (
    BookingListCreateView,
    BookingDetailView,
    LockSlotView,
    CancelBookingView,
    RescheduleBookingView,
    PricePreviewView,
    StaffTodayBookingsView,
    StaffWalkInBookingView,
    RecordOfflinePaymentView,
)

urlpatterns = [
    path("", BookingListCreateView.as_view(), name="booking_list_create"),
    path("preview-price/", PricePreviewView.as_view(), name="preview_price"),
    path("price-preview/", PricePreviewView.as_view(), name="price_preview_alias"),
    path("lock/", LockSlotView.as_view(), name="lock_slots_alias"),
    path("lock-slots/", LockSlotView.as_view(), name="lock_slots"),
    path("staff/today/", StaffTodayBookingsView.as_view(), name="staff_today"),
    path("staff/walk-in/", StaffWalkInBookingView.as_view(), name="staff_walk_in"),
    path("walk-in/", StaffWalkInBookingView.as_view(), name="walk_in_alias"),
    path("<str:identifier>/", BookingDetailView.as_view(), name="booking_detail"),
    path(
        "<str:identifier>/cancel/", CancelBookingView.as_view(), name="cancel_booking"
    ),
    path(
        "<str:identifier>/reschedule/",
        RescheduleBookingView.as_view(),
        name="reschedule_booking",
    ),
    path(
        "<str:identifier>/offline-payment/",
        RecordOfflinePaymentView.as_view(),
        name="record_offline_payment",
    ),
]

