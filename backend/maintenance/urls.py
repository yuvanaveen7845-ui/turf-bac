from django.urls import path
from .views import MaintenanceListCreateView, MaintenanceDetailView

urlpatterns = [
    path("", MaintenanceListCreateView.as_view(), name="maintenance_list"),
    path("<str:pk>/", MaintenanceDetailView.as_view(), name="maintenance_detail"),
]
