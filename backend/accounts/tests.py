import uuid
from unittest.mock import patch
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient
from rest_framework import status

from .models import User, CustomerProfile, StaffProfile, PasswordResetToken, BusinessSetting
from .permissions import (
    ROLE_PERMISSIONS,
    user_has_permission,
    get_user_permissions,
    IsOwnerOrBusinessUser,
)
from audit.models import AuditLog


class AccountsAuthTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.password = "Secur3P@ssw0rd!"

        # Create Admin
        self.admin_user = User.objects.create_user(
            email="admin@friendsturf.com",
            password=self.password,
            first_name="Admin",
            last_name="Boss",
            role="ADMIN",
            status="ACTIVE",
        )

        # Create Staff
        self.staff_user = User.objects.create_user(
            email="staff@friendsturf.com",
            password=self.password,
            first_name="Staff",
            last_name="Member",
            role="STAFF",
            status="ACTIVE",
        )

        # Create Customer
        self.customer_user = User.objects.create_user(
            email="player@example.com",
            password=self.password,
            first_name="Player",
            last_name="One",
            role="CUSTOMER",
            status="ACTIVE",
        )

    def test_email_password_login_success(self):
        response = self.client.post(
            "/api/auth/login/",
            {"email": "player@example.com", "password": self.password},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn("tokens", response.data)
        self.assertIn("access", response.data["tokens"])
        self.assertEqual(response.data["user"]["role"], "CUSTOMER")
        self.assertIn("permissions", response.data["user"])
        self.assertIn("BOOKING_CREATE", response.data["user"]["permissions"])

    def test_email_password_login_invalid_credentials(self):
        response = self.client.post(
            "/api/auth/login/",
            {"email": "player@example.com", "password": "WrongPassword123"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_public_registration_cannot_self_select_role(self):
        """Users attempting to register with role=ADMIN or role=STAFF must be forced to CUSTOMER."""
        response = self.client.post(
            "/api/auth/register/",
            {
                "email": "hacker@example.com",
                "password": "Password123!",
                "first_name": "Fake",
                "last_name": "Admin",
                "role": "ADMIN",  # Attempted self-promotion
            },
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["user"]["role"], "CUSTOMER")

        created_user = User.objects.get(email="hacker@example.com")
        self.assertEqual(created_user.role, "CUSTOMER")

    def test_suspended_and_disabled_user_login_blocked(self):
        """Suspended and disabled users cannot log in and have no permissions."""
        suspended_user = User.objects.create_user(
            email="suspended@example.com",
            password=self.password,
            first_name="Suspended",
            role="STAFF",
            status="SUSPENDED",
        )
        # Login must be rejected
        response = self.client.post(
            "/api/auth/login/",
            {"email": "suspended@example.com", "password": self.password},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

        # Permissions check must be empty
        self.assertEqual(get_user_permissions(suspended_user), set())
        self.assertFalse(user_has_permission(suspended_user, "BOOKING_VIEW"))

    def test_enterprise_permissions_hierarchy(self):
        """Verify strict permission boundaries across Customer, Staff, and Admin."""
        # CUSTOMER
        self.assertTrue(user_has_permission(self.customer_user, "BOOKING_CREATE"))
        self.assertFalse(user_has_permission(self.customer_user, "BOOKING_VIEW"))  # Cannot view all bookings
        self.assertFalse(user_has_permission(self.customer_user, "PAYMENT_REFUND"))
        self.assertFalse(user_has_permission(self.customer_user, "PRICING_EDIT"))

        # STAFF
        self.assertTrue(user_has_permission(self.staff_user, "BOOKING_VIEW"))
        self.assertTrue(user_has_permission(self.staff_user, "CHECKIN_SCAN"))
        self.assertTrue(user_has_permission(self.staff_user, "PAYMENT_RECORD_OFFLINE"))
        self.assertFalse(user_has_permission(self.staff_user, "PAYMENT_REFUND"))  # Refunds require Admin
        self.assertFalse(user_has_permission(self.staff_user, "PRICING_EDIT"))
        self.assertFalse(user_has_permission(self.staff_user, "STAFF_CREATE"))

        # ADMIN
        self.assertTrue(user_has_permission(self.admin_user, "BOOKING_VIEW"))
        self.assertTrue(user_has_permission(self.admin_user, "PAYMENT_REFUND"))
        self.assertTrue(user_has_permission(self.admin_user, "PRICING_EDIT"))
        self.assertTrue(user_has_permission(self.admin_user, "PRICING_OVERRIDE"))
        self.assertTrue(user_has_permission(self.admin_user, "STAFF_CREATE"))
        self.assertTrue(user_has_permission(self.admin_user, "STAFF_SUSPEND"))
        self.assertTrue(user_has_permission(self.admin_user, "REPORT_VIEW"))
        self.assertTrue(user_has_permission(self.admin_user, "AUDIT_VIEW"))
        self.assertTrue(user_has_permission(self.admin_user, "SETTINGS_EDIT"))

    def test_customer_cannot_update_own_role(self):
        """Customers calling PUT /api/auth/me/ cannot elevate their role to ADMIN."""
        self.client.force_authenticate(user=self.customer_user)
        response = self.client.put(
            "/api/auth/me/",
            {"role": "ADMIN", "first_name": "NewName"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.customer_user.refresh_from_db()
        self.assertEqual(self.customer_user.role, "CUSTOMER")
        self.assertEqual(self.customer_user.first_name, "NewName")

    def test_feature_flags_endpoint(self):
        """Verify GET and PUT on /api/auth/features/."""
        # 1. Public GET
        get_res = self.client.get("/api/auth/features/")
        self.assertEqual(get_res.status_code, status.HTTP_200_OK)
        self.assertIn("RECURRING_BOOKINGS", get_res.data)
        self.assertIn("DYNAMIC_PRICING", get_res.data)

        # 2. Customer PUT denied
        self.client.force_authenticate(user=self.customer_user)
        put_denied = self.client.put(
            "/api/auth/features/",
            {"DYNAMIC_PRICING": False},
            format="json",
        )
        self.assertEqual(put_denied.status_code, status.HTTP_403_FORBIDDEN)

        # 3. Admin PUT succeeds
        self.client.force_authenticate(user=self.admin_user)
        put_success = self.client.put(
            "/api/auth/features/",
            {"DYNAMIC_PRICING": False, "RECURRING_BOOKINGS": True},
            format="json",
        )
        self.assertEqual(put_success.status_code, status.HTTP_200_OK)
        self.assertFalse(put_success.data["DYNAMIC_PRICING"])

        # Check persistence
        setting = BusinessSetting.objects.get(key="features")
        self.assertFalse(setting.value["DYNAMIC_PRICING"])

        # Check AuditLog
        audit = AuditLog.objects.filter(action="FEATURE_FLAGS_UPDATED").first()
        self.assertIsNotNone(audit)
        self.assertEqual(audit.user, self.admin_user)

    @patch("accounts.views.id_token.verify_oauth2_token")
    def test_google_oauth_auto_provision_customer(self, mock_verify):
        mock_verify.return_value = {
            "email": "newgoogleuser@example.com",
            "sub": "google-sub-123456",
            "name": "Alex Hunter",
            "given_name": "Alex",
            "family_name": "Hunter",
            "picture": "https://lh3.googleusercontent.com/a/photo.jpg",
            "email_verified": True,
        }

        response = self.client.post(
            "/api/auth/google/",
            {"credential": "mock_google_jwt_credential"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn("tokens", response.data)
        self.assertEqual(response.data["user"]["role"], "CUSTOMER")
        self.assertEqual(response.data["user"]["email"], "newgoogleuser@example.com")

        # Verify in database
        user = User.objects.filter(email="newgoogleuser@example.com").first()
        self.assertIsNotNone(user)
        self.assertEqual(user.role, "CUSTOMER")
        self.assertEqual(user.google_id, "google-sub-123456")
        self.assertTrue(hasattr(user, "customer_profile"))

    @patch("accounts.views.id_token.verify_oauth2_token")
    def test_google_oauth_existing_business_user_preserves_role(self, mock_verify):
        mock_verify.return_value = {
            "email": "admin@friendsturf.com",
            "sub": "google-sub-admin-999",
            "name": "Admin Boss",
            "picture": "https://lh3.googleusercontent.com/admin.jpg",
            "email_verified": True,
        }

        response = self.client.post(
            "/api/auth/google/",
            {"credential": "mock_google_jwt_admin_credential"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        # Role must remain ADMIN, not changed to CUSTOMER
        self.assertEqual(response.data["user"]["role"], "ADMIN")

        self.admin_user.refresh_from_db()
        self.assertEqual(self.admin_user.google_id, "google-sub-admin-999")
        self.assertEqual(self.admin_user.role, "ADMIN")
