from django.urls import path
from .views import (
    MaintenanceListCreateView,
    MaintenanceDetailView,
    MaintenanceConflictCheckView,
)

urlpatterns = [
    path("", MaintenanceListCreateView.as_view(), name="maintenance_list"),
    path("check-conflicts/", MaintenanceConflictCheckView.as_view(), name="maintenance_check_conflicts"),
    path("<str:pk>/", MaintenanceDetailView.as_view(), name="maintenance_detail"),
]

