from django.urls import path
from .views import (
    PricingRuleListCreateView,
    PricingRuleDetailView,
    HolidayListCreateView,
    SpecialEventListCreateView,
)

urlpatterns = [
    path("rules/", PricingRuleListCreateView.as_view(), name="pricing_rule_list"),
    path(
        "rules/<str:pk>/", PricingRuleDetailView.as_view(), name="pricing_rule_detail"
    ),
    path("holidays/", HolidayListCreateView.as_view(), name="holiday_list"),
    path("events/", SpecialEventListCreateView.as_view(), name="event_list"),
]
