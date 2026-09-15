from django.urls import path
from .views import AdminDashboardMetricsView, BusinessReportsView

urlpatterns = [
    path(
        "dashboard/",
        AdminDashboardMetricsView.as_view(),
        name="admin_dashboard_metrics",
    ),
    path("daily-summary/", BusinessReportsView.as_view(), name="daily_summary_report"),
]
