from django.urls import path
from rest_framework_simplejwt.views import TokenRefreshView
from .views import (
    RegisterView,
    LoginView,
    CurrentUserView,
    GoogleAuthView,
    ForgotPasswordView,
    ResetPasswordView,
    AdminCustomerListView,
    AdminB2BUserListView,
    AdminB2BUserDetailView,
    BusinessSettingsView,
    AdminCustomerDetailView,
    AdminCustomerNoteView,
    FeatureFlagsView,
)

urlpatterns = [
    path("google/", GoogleAuthView.as_view(), name="google_auth"),
    path("register/", RegisterView.as_view(), name="register"),
    path("login/", LoginView.as_view(), name="login"),
    path("forgot-password/", ForgotPasswordView.as_view(), name="forgot_password"),
    path("reset-password/", ResetPasswordView.as_view(), name="reset_password"),
    path("refresh/", TokenRefreshView.as_view(), name="token_refresh"),
    path("me/", CurrentUserView.as_view(), name="current_user"),
    path("features/", FeatureFlagsView.as_view(), name="feature_flags"),
    path("settings/", BusinessSettingsView.as_view(), name="business_settings"),
    path("customers/", AdminCustomerListView.as_view(), name="admin_customers"),
    path("customers/<str:pk>/", AdminCustomerDetailView.as_view(), name="admin_customer_detail"),
    path("customers/<str:pk>/notes/", AdminCustomerNoteView.as_view(), name="admin_customer_notes"),
    path("customers/<str:pk>/notes/<int:note_id>/", AdminCustomerNoteView.as_view(), name="admin_customer_note_delete"),
    path("admin/customers/", AdminCustomerListView.as_view(), name="admin_customers_legacy"),
    path("b2b-users/", AdminB2BUserListView.as_view(), name="b2b_users_list_create"),
    path("b2b-users/<str:pk>/", AdminB2BUserDetailView.as_view(), name="b2b_users_detail"),
    path("admin/staff/", AdminB2BUserListView.as_view(), name="admin_staff_legacy"),
]

