from django.urls import path
from .views import (
    PricingRuleListCreateView,
    PricingRuleDetailView,
    HolidayListCreateView,
    SpecialEventListCreateView,
    PricingConflictDetectView,
    PricingSimulateView,
)

urlpatterns = [
    path("rules/", PricingRuleListCreateView.as_view(), name="pricing_rule_list"),
    path("rules/conflicts/", PricingConflictDetectView.as_view(), name="pricing_rule_conflicts"),
    path("rules/simulate/", PricingSimulateView.as_view(), name="pricing_rule_simulate"),
    path("rules/<str:pk>/", PricingRuleDetailView.as_view(), name="pricing_rule_detail"),
    path("holidays/", HolidayListCreateView.as_view(), name="holiday_list"),
    path("events/", SpecialEventListCreateView.as_view(), name="event_list"),
]

