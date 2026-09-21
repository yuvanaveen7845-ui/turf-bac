from django.urls import path
from .views import (
    AdminDashboardMetricsView,
    BusinessReportsView,
    ExportReportsCSVView,
    GlobalSearchView,
    FinancialReconciliationView,
    DailyOperationsView,
    DailyCloseSummaryView,
    SystemHealthView,
    OperationsControlCenterOverviewView,
    OperationsReleaseHoldView,
    OperationsMarkNoShowView,
)

urlpatterns = [
    path("dashboard/", AdminDashboardMetricsView.as_view(), name="admin_dashboard_metrics"),
    path("daily-summary/", BusinessReportsView.as_view(), name="daily_summary_report"),
    path("export-csv/", ExportReportsCSVView.as_view(), name="export_reports_csv"),
    path("global-search/", GlobalSearchView.as_view(), name="global_search"),
    path("reconciliation/", FinancialReconciliationView.as_view(), name="financial_reconciliation"),
    path("daily-operations/", DailyOperationsView.as_view(), name="daily_operations"),
    path("daily-close/", DailyCloseSummaryView.as_view(), name="daily_close"),
    path("system-health/", SystemHealthView.as_view(), name="system_health"),
    path("operations/overview/", OperationsControlCenterOverviewView.as_view(), name="operations_overview"),
    path("operations/release-hold/", OperationsReleaseHoldView.as_view(), name="operations_release_hold"),
    path("operations/mark-no-show/", OperationsMarkNoShowView.as_view(), name="operations_mark_no_show"),
]


