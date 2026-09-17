from django.urls import path
from .views import NotificationListView, NotificationDetailView, SendMatchPassEmailView

urlpatterns = [
    path("", NotificationListView.as_view(), name="notification_list"),
    path("send-match-pass/", SendMatchPassEmailView.as_view(), name="send_match_pass_email"),
    path("<str:pk>/read/", NotificationDetailView.as_view(), name="notification_read"),
]

