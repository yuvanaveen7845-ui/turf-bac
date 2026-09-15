from django.urls import path
from .views import ReviewListCreateView, ReviewDetailView, ReviewAnalyticsView

urlpatterns = [
    path("", ReviewListCreateView.as_view(), name="review_list_create"),
    path("analytics/", ReviewAnalyticsView.as_view(), name="review_analytics"),
    path("<str:pk>/", ReviewDetailView.as_view(), name="review_detail"),
]
