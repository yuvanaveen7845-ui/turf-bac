from django.urls import path
from .views import NotificationListView, NotificationDetailView

urlpatterns = [
    path("", NotificationListView.as_view(), name="notification_list"),
    path("<str:pk>/read/", NotificationDetailView.as_view(), name="notification_read"),
]
