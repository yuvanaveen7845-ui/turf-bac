from django.urls import path
from .views import MembershipPlanListView, CustomerMembershipView

urlpatterns = [
    path("plans/", MembershipPlanListView.as_view(), name="membership_plan_list"),
    path(
        "my-membership/", CustomerMembershipView.as_view(), name="customer_membership"
    ),
]
