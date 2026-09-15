from django.urls import path
from .views import FacilityListView, TurfListView, TurfDetailView, TurfAvailabilityView

urlpatterns = [
    path("facilities/", FacilityListView.as_view(), name="facility_list"),
    path("", TurfListView.as_view(), name="turf_list"),
    path("<str:pk>/", TurfDetailView.as_view(), name="turf_detail"),
    path(
        "<str:pk>/availability/",
        TurfAvailabilityView.as_view(),
        name="turf_availability",
    ),
]
