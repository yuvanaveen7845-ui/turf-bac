from django.urls import path
from .views import (
    FacilityListView,
    TurfListView,
    TurfDetailView,
    TurfAvailabilityView,
    DailyScheduleView,
    TurfImageUploadView,
)

urlpatterns = [
    path("facilities/", FacilityListView.as_view(), name="facility_list"),
    path("upload-image/", TurfImageUploadView.as_view(), name="turf_image_upload"),
    path("schedule/", DailyScheduleView.as_view(), name="daily_schedule"),
    path("", TurfListView.as_view(), name="turf_list"),
    path("<str:pk>/", TurfDetailView.as_view(), name="turf_detail"),
    path(
        "<str:pk>/availability/",
        TurfAvailabilityView.as_view(),
        name="turf_availability",
    ),
    path(
        "<str:pk>/slots/",
        TurfAvailabilityView.as_view(),
        name="turf_slots",
    ),
]
