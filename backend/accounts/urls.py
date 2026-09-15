from django.urls import path
from rest_framework_simplejwt.views import TokenRefreshView
from .views import (
    RegisterView,
    LoginView,
    CurrentUserView,
    AdminCustomerListView,
    AdminStaffView,
)

urlpatterns = [
    path("register/", RegisterView.as_view(), name="register"),
    path("login/", LoginView.as_view(), name="login"),
    path("refresh/", TokenRefreshView.as_view(), name="token_refresh"),
    path("me/", CurrentUserView.as_view(), name="current_user"),
    path("admin/customers/", AdminCustomerListView.as_view(), name="admin_customers"),
    path("admin/staff/", AdminStaffView.as_view(), name="admin_staff"),
]
