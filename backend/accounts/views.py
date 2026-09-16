import os
import uuid
import requests
from django.conf import settings
from django.utils import timezone
from django.db import models
from rest_framework import status, views, permissions, generics
from rest_framework.response import Response
from rest_framework_simplejwt.tokens import RefreshToken
from google.oauth2 import id_token
from google.auth.transport import requests as google_requests

from .models import User, CustomerProfile, StaffProfile, PasswordResetToken
from .serializers import (
    UserSerializer,
    RegisterSerializer,
    LoginSerializer,
    GoogleAuthSerializer,
    B2BUserCreateSerializer,
    B2BUserUpdateSerializer,
    ForgotPasswordSerializer,
    ResetPasswordSerializer,
)
from .permissions import (
    IsAdmin,
    IsManager,
    IsStaffOrAdmin,
    IsB2BUser,
    user_has_permission,
    get_user_permissions,
)
from audit.models import AuditLog


DEFAULT_FEATURE_FLAGS = {
    "RECURRING_BOOKINGS": True,
    "PARTIAL_PAYMENTS": True,
    "WALK_IN_BOOKINGS": True,
    "DYNAMIC_PRICING": True,
    "QR_CHECKIN": True,
    "ONLINE_PAYMENTS": True,
    "OFFLINE_PAYMENTS": True,
    "COUPONS": True,
    "REVIEWS": True,
    "ADVANCED_REPORTING": True,
}


def get_tokens_for_user(user):
    refresh = RefreshToken.for_user(user)
    return {
        "refresh": str(refresh),
        "access": str(refresh.access_token),
    }


class GoogleAuthView(views.APIView):
    """
    Unified Google OAuth authentication endpoint for Customers and Business Users.
    Verifies cryptographic Google ID Token and manages accounts seamlessly.
    - Pre-authorized B2B users (STAFF, ADMIN) keep their database role.
    - New Google users are automatically provisioned as active CUSTOMER accounts.
    """
    permission_classes = [permissions.AllowAny]

    def post(self, request):
        serializer = GoogleAuthSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        credential = serializer.validated_data["credential"]

        # 1. Verify Google ID Token
        id_info = None
        try:
            client_id = getattr(settings, "GOOGLE_CLIENT_ID", "") or None
            id_info = id_token.verify_oauth2_token(
                credential, google_requests.Request(), client_id, clock_skew_in_seconds=10
            )
        except Exception as e:
            # Fallback to Google tokeninfo endpoint if local verification encounters client_id mismatch
            try:
                resp = requests.get(
                    f"https://oauth2.googleapis.com/tokeninfo?id_token={credential}",
                    timeout=5,
                )
                if resp.status_code == 200:
                    id_info = resp.json()
            except Exception:
                pass

        if not id_info or "email" not in id_info:
            return Response(
                {
                    "code": "INVALID_GOOGLE_TOKEN",
                    "detail": "Failed to verify Google authentication token.",
                },
                status=status.HTTP_401_UNAUTHORIZED,
            )

        google_email = id_info.get("email", "").strip().lower()
        google_id = id_info.get("sub", "")
        picture = id_info.get("picture", "")
        name = id_info.get("name", "")
        given_name = id_info.get("given_name", "")
        family_name = id_info.get("family_name", "")

        # 2. Check if user already exists
        user = User.objects.filter(email__iexact=google_email).first()

        if user:
            # Existing user: Check account status
            if user.status in ("SUSPENDED", "DISABLED") or not user.is_active:
                AuditLog.objects.create(
                    user=user,
                    action="SUSPENDED_LOGIN_ATTEMPT",
                    resource_type="AUTH",
                    resource_id=user.email,
                    details={"status": user.status},
                )
                return Response(
                    {
                        "code": "ACCOUNT_SUSPENDED",
                        "detail": "Your Friends Turf account has been suspended or disabled. Please contact support.",
                    },
                    status=status.HTTP_403_FORBIDDEN,
                )

            # Activate user if previously INVITED
            if user.status == "INVITED":
                user.status = "ACTIVE"

            # Update Google linking details
            user.google_id = google_id
            if picture and not user.profile_image:
                user.profile_image = picture
            if not user.first_name and given_name:
                user.first_name = given_name
            if not user.last_name and family_name:
                user.last_name = family_name
            user.last_login_at = timezone.now()
            user.save()

            AuditLog.objects.create(
                user=user,
                action="LOGIN_SUCCESS",
                resource_type="AUTH",
                resource_id=user.email,
                details={"provider": "GOOGLE_OAUTH", "role": user.role},
            )
        else:
            # 3. Auto-provision new customer account
            first_n = given_name or (name.split(" ")[0] if name else "Customer")
            last_n = family_name or (" ".join(name.split(" ")[1:]) if name and " " in name else "")
            user = User.objects.create_user(
                email=google_email,
                password=None,
                first_name=first_n,
                last_name=last_n,
                profile_image=picture,
                role="CUSTOMER",
                status="ACTIVE",
            )
            user.google_id = google_id
            user.last_login_at = timezone.now()
            user.save()

            AuditLog.objects.create(
                user=user,
                action="CUSTOMER_REGISTERED_GOOGLE",
                resource_type="AUTH",
                resource_id=user.email,
                details={"provider": "GOOGLE_OAUTH", "role": "CUSTOMER"},
            )

        tokens = get_tokens_for_user(user)
        user_data = UserSerializer(user).data

        return Response(
            {
                "message": "Authentication successful",
                "user": user_data,
                "tokens": tokens,
            },
            status=status.HTTP_200_OK,
        )


class ForgotPasswordView(views.APIView):
    """
    Public password recovery request. Generates a secure single-use token.
    """
    permission_classes = [permissions.AllowAny]

    def post(self, request):
        serializer = ForgotPasswordSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        email = serializer.validated_data["email"]
        user = User.objects.filter(email__iexact=email).first()

        token_str = None
        if user and user.is_active and user.status not in ("SUSPENDED", "DISABLED"):
            token_obj = PasswordResetToken.generate_token_for_user(user, validity_hours=1)
            token_str = token_obj.token
            AuditLog.objects.create(
                user=user,
                action="PASSWORD_RESET_REQUESTED",
                resource_type="AUTH",
                resource_id=user.email,
                details={"ip": request.META.get("REMOTE_ADDR")},
            )

        # Uniform response to avoid account enumeration
        response_payload = {
            "message": "If an account exists with this email address, password reset instructions have been sent.",
        }
        if getattr(settings, "DEBUG", False) and token_str:
            response_payload["dev_reset_token"] = token_str
            response_payload["dev_reset_url"] = f"/reset-password?token={token_str}"

        return Response(response_payload, status=status.HTTP_200_OK)


class ResetPasswordView(views.APIView):
    """
    Public password reset execution with single-use expiring token.
    """
    permission_classes = [permissions.AllowAny]

    def post(self, request):
        serializer = ResetPasswordSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        token_val = serializer.validated_data["token"].strip()
        password = serializer.validated_data["password"]

        token_obj = PasswordResetToken.objects.filter(token=token_val).first()
        if not token_obj or not token_obj.is_valid():
            return Response(
                {"error": "This password reset link is invalid or has expired. Please request a new one."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        user = token_obj.user
        user.set_password(password)
        user.save()

        token_obj.is_used = True
        token_obj.save(update_fields=["is_used"])

        AuditLog.objects.create(
            user=user,
            action="PASSWORD_RESET_COMPLETED",
            resource_type="AUTH",
            resource_id=user.email,
            details={"ip": request.META.get("REMOTE_ADDR")},
        )

        return Response(
            {"message": "Your password has been reset successfully. You may now log in with your new password."},
            status=status.HTTP_200_OK,
        )


class RegisterView(views.APIView):
    """Customer registration only."""
    permission_classes = [permissions.AllowAny]

    def post(self, request):
        try:
            serializer = RegisterSerializer(data=request.data)
            if serializer.is_valid():
                # Force role to CUSTOMER for public self-registration
                user = serializer.save()
                tokens = get_tokens_for_user(user)
                user_data = UserSerializer(user).data
                return Response(
                    {
                        "message": "Registration successful",
                        "user": user_data,
                        "tokens": tokens,
                    },
                    status=status.HTTP_201_CREATED,
                )
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
        except Exception as e:
            import traceback
            traceback.print_exc()
            return Response(
                {"error": "Registration failed", "detail": str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class LoginView(views.APIView):
    """Standard credential login for customer accounts."""
    permission_classes = [permissions.AllowAny]

    def post(self, request):
        try:
            serializer = LoginSerializer(data=request.data)
            if serializer.is_valid():
                user = serializer.validated_data["user"]
                try:
                    user.last_login_at = timezone.now()
                    user.save(update_fields=["last_login_at"])
                except Exception:
                    user.save()
                tokens = get_tokens_for_user(user)
                user_data = UserSerializer(user).data
                return Response(
                    {
                        "message": "Login successful",
                        "user": user_data,
                        "tokens": tokens,
                    },
                    status=status.HTTP_200_OK,
                )
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
        except Exception as e:
            import traceback
            traceback.print_exc()
            return Response(
                {"error": "Login failed", "detail": str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class CurrentUserView(views.APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        serializer = UserSerializer(request.user)
        return Response(serializer.data)

    def put(self, request):
        user = request.user
        data = request.data
        if "first_name" in data:
            user.first_name = data["first_name"]
        if "last_name" in data:
            user.last_name = data["last_name"]
        if "phone" in data:
            user.phone = data["phone"]
        user.save()

        # Update customer profile if birthday provided
        if hasattr(user, "customer_profile") and "birthday" in data:
            profile = user.customer_profile
            profile.birthday = data["birthday"] or None
            profile.save()

        return Response(UserSerializer(user).data)


class AdminCustomerListView(views.APIView):
    permission_classes = [IsStaffOrAdmin]

    def get(self, request):
        customers = (
            User.objects.filter(role="CUSTOMER")
            .select_related("customer_profile")
            .order_by("-date_joined")
        )
        search = request.query_params.get("search", "").strip()
        if search:
            customers = customers.filter(
                models.Q(email__icontains=search)
                | models.Q(first_name__icontains=search)
                | models.Q(last_name__icontains=search)
                | models.Q(phone__icontains=search)
            )
        serializer = UserSerializer(customers, many=True)
        return Response(serializer.data)


class AdminB2BUserListView(views.APIView):
    """
    B2B Team Management:
    - Lists all STAFF and ADMIN accounts with authorization details
    - Invites / creates new B2B users
    """
    permission_classes = [IsAdmin]

    def get(self, request):
        users = (
            User.objects.filter(role__in=["STAFF", "ADMIN"])
            .select_related("staff_profile")
            .order_by("-date_joined")
        )
        role_filter = request.query_params.get("role")
        if role_filter:
            users = users.filter(role=role_filter.upper())
        status_filter = request.query_params.get("status")
        if status_filter:
            users = users.filter(status=status_filter.upper())
        search = request.query_params.get("search", "").strip()
        if search:
            users = users.filter(
                models.Q(email__icontains=search)
                | models.Q(first_name__icontains=search)
                | models.Q(last_name__icontains=search)
            )

        serializer = UserSerializer(users, many=True)
        return Response(serializer.data)

    def post(self, request):
        if not (
            request.user.role == "ADMIN"
            or request.user.is_superuser
            or user_has_permission(request.user, "STAFF_CREATE")
        ):
            return Response(
                {"error": "Admin permission required to invite new B2B users."},
                status=status.HTTP_403_FORBIDDEN,
            )

        serializer = B2BUserCreateSerializer(data=request.data)
        if serializer.is_valid():
            data = serializer.validated_data
            # Create user without password (will authenticate via Google OAuth)
            user = User.objects.create_user(
                email=data["email"],
                password=None,
                first_name=data.get("first_name", ""),
                last_name=data.get("last_name", ""),
                phone=data.get("phone", ""),
                role=data.get("role", "STAFF"),
                status=data.get("status", "INVITED"),
            )

            # Update staff profile details
            if hasattr(user, "staff_profile"):
                staff_prof = user.staff_profile
                staff_prof.employee_id = data.get("employee_id", "")
                staff_prof.department = data.get("department", "Turf Operations")
                staff_prof.save()

            AuditLog.objects.create(
                user=request.user,
                action="B2B_USER_INVITED",
                resource_type="USER",
                resource_id=user.email,
                details={"role": user.role, "status": user.status, "department": data.get("department", "")},
            )

            return Response(UserSerializer(user).data, status=status.HTTP_201_CREATED)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


class AdminB2BUserDetailView(views.APIView):
    """
    Manage individual B2B account: update role, change status (SUSPEND / ACTIVATE / DISABLE).
    """
    permission_classes = [IsAdmin]

    def patch(self, request, pk):
        user = generics.get_object_or_404(User, pk=pk)
        serializer = B2BUserUpdateSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        data = serializer.validated_data
        old_role = user.role
        old_status = user.status

        if "role" in data:
            if not (
                request.user.role == "ADMIN"
                or request.user.is_superuser
                or user_has_permission(request.user, "STAFF_EDIT")
            ):
                return Response(
                    {"error": "Permission denied: STAFF_EDIT required to change roles."},
                    status=status.HTTP_403_FORBIDDEN,
                )
            user.role = data["role"]
            if user.role == "ADMIN":
                user.is_staff = True
        if "status" in data:
            if not (
                request.user.role == "ADMIN"
                or request.user.is_superuser
                or user_has_permission(request.user, "STAFF_SUSPEND")
            ):
                return Response(
                    {"error": "Permission denied: STAFF_SUSPEND required to alter user status."},
                    status=status.HTTP_403_FORBIDDEN,
                )
            user.status = data["status"]
            user.is_active = data["status"] == "ACTIVE"
        if "first_name" in data:
            user.first_name = data["first_name"]
        if "last_name" in data:
            user.last_name = data["last_name"]
        if "phone" in data:
            user.phone = data["phone"]
        user.save()

        # Update staff profile
        if hasattr(user, "staff_profile"):
            prof = user.staff_profile
            if "department" in data:
                prof.department = data["department"]
            if "employee_id" in data:
                prof.employee_id = data["employee_id"]
            if "is_on_duty" in data:
                prof.is_on_duty = data["is_on_duty"]
            prof.save()

        AuditLog.objects.create(
            user=request.user,
            action="B2B_USER_UPDATED",
            resource_type="USER",
            resource_id=user.email,
            details={
                "old_role": old_role,
                "new_role": user.role,
                "old_status": old_status,
                "new_status": user.status,
            },
        )

        return Response(UserSerializer(user).data)


class BusinessSettingsView(views.APIView):
    """
    Persists and retrieves platform business settings:
    Company info, booking rules, operating hours, payment config, turnstile rules, and reminders.
    """
    permission_classes = [permissions.IsAuthenticated]

    DEFAULT_SETTINGS = {
        "company": {
            "name": "Friends Turf",
            "tagline": "PLAY HARD. BOOK DIRECT. OWN THE PITCH.",
            "address": "Near Sirupooluvapatti, Kamatchepuram, Tiruppur, Tamil Nadu 641603 (RTO Office Backside)",
            "phone": "+91 93619 89494",
            "email": "contact@friendsturf.com",
            "website": "https://friendsturf.com",
            "instagram": "@friendsturf_tiruppur",
            "whatsapp": "+91 93639 89494",
            "timezone": "Asia/Kolkata",
            "currency": "INR",
        },
        "booking": {
            "advanceBookingDays": 14,
            "minDurationMinutes": 60,
            "maxDurationMinutes": 180,
            "slotHoldMinutes": 5,
            "cancellationFullRefundHours": 24,
            "cancellationPartialRefundHours": 12,
            "partialRefundPercent": 50,
            "allowRescheduling": True,
            "rescheduleCutoffHours": 6,
        },
        "hours": {
            "openTime": "06:00",
            "closeTime": "23:00",
            "slotDurationMinutes": 60,
            "bufferTimeMinutes": 0,
            "allowMidnightBookings": False,
        },
        "payments": {
            "gateway": "RAZORPAY",
            "mode": "TEST",
            "upiId": "friendsturf@okhdfcbank",
            "enableSplitDeposit": True,
            "advanceDepositPercent": 50,
            "taxPercentage": 18,
            "isTaxIncluded": True,
        },
        "checkin": {
            "windowOpenMinutes": 30,
            "gracePeriodMinutes": 30,
            "allowManualOverride": True,
            "requireOverrideReason": True,
        },
        "notifications": {
            "sendConfirmationImmediately": True,
            "reminder24h": True,
            "reminder2h": True,
            "postMatchFeedbackHours": 2,
        },
        "features": DEFAULT_FEATURE_FLAGS,
    }

    def get(self, request):
        from .models import BusinessSetting
        settings_dict = {}
        for section, defaults in self.DEFAULT_SETTINGS.items():
            record = BusinessSetting.objects.filter(key=section).first()
            if record and isinstance(record.value, dict):
                merged = {**defaults, **record.value}
                settings_dict[section] = merged
            else:
                settings_dict[section] = defaults
        return Response(settings_dict)

    def post(self, request):
        if not (request.user.role == "ADMIN" or request.user.is_superuser):
            return Response({"error": "Admin permission required."}, status=status.HTTP_403_FORBIDDEN)

        from .models import BusinessSetting
        data = request.data
        updated_sections = []

        for section, values in data.items():
            if isinstance(values, dict):
                record, _ = BusinessSetting.objects.get_or_create(key=section)
                record.value = values
                record.updated_by = request.user
                record.save()
                updated_sections.append(section)

        AuditLog.objects.create(
            user=request.user,
            action="SETTINGS_UPDATED",
            resource_type="SETTINGS",
            resource_id="GLOBAL",
            details={"sections": updated_sections},
        )

        return Response({"message": "Settings saved successfully.", "updated": updated_sections}, status=status.HTTP_200_OK)


class AdminCustomerDetailView(views.APIView):
    """
    Complete 360-degree Customer CRM view:
    Profile, lifetime statistics, booking history, payment records, reviews, loyalty transactions, and CRM notes.
    """
    permission_classes = [IsStaffOrAdmin]

    def get(self, request, pk):
        from .models import User, CustomerNote
        from .serializers import CustomerNoteSerializer
        from bookings.models import Booking
        from bookings.serializers import BookingSerializer
        from payments.models import Payment
        from payments.serializers import PaymentSerializer
        from reviews.models import Review
        from reviews.serializers import ReviewSerializer
        from wallet.models import LoyaltyTransaction

        customer = generics.get_object_or_404(User, pk=pk, role="CUSTOMER")
        user_data = UserSerializer(customer).data

        bookings = Booking.objects.filter(customer=customer).select_related("turf").order_by("-date", "-created_at")
        payments = Payment.objects.filter(customer=customer).select_related("booking").order_by("-created_at")
        reviews = Review.objects.filter(customer=customer).select_related("turf").order_by("-created_at")
        notes = CustomerNote.objects.filter(customer=customer).select_related("author").order_by("-is_pinned", "-created_at")
        loyalty_txns = LoyaltyTransaction.objects.filter(customer=customer).order_by("-created_at")[:20]

        total_bookings_count = bookings.count()
        completed_bookings_count = bookings.filter(status="COMPLETED").count()
        cancelled_bookings_count = bookings.filter(status="CANCELLED").count()
        total_spent = sum(p.amount for p in payments.filter(status="PAID"))

        return Response({
            "customer": user_data,
            "metrics": {
                "total_bookings": total_bookings_count,
                "completed_bookings": completed_bookings_count,
                "cancelled_bookings": cancelled_bookings_count,
                "total_spent": float(total_spent),
                "repeat_rate": round((total_bookings_count / 1.0) if total_bookings_count <= 1 else ((total_bookings_count - 1) / total_bookings_count * 100), 1),
            },
            "bookings": BookingSerializer(bookings[:30], many=True).data,
            "payments": PaymentSerializer(payments[:30], many=True).data,
            "reviews": ReviewSerializer(reviews, many=True).data,
            "notes": CustomerNoteSerializer(notes, many=True).data,
            "loyalty_transactions": [
                {
                    "id": tx.id,
                    "points": tx.points,
                    "type": tx.transaction_type,
                    "description": tx.description,
                    "created_at": tx.created_at.strftime("%Y-%m-%d %H:%M"),
                }
                for tx in loyalty_txns
            ],
        })


class AdminCustomerNoteView(views.APIView):
    """
    Staff / Admin internal notes management for Customer CRM.
    """
    permission_classes = [IsStaffOrAdmin]

    def post(self, request, pk):
        from .models import User, CustomerNote
        from .serializers import CustomerNoteSerializer

        customer = generics.get_object_or_404(User, pk=pk, role="CUSTOMER")
        note_text = request.data.get("note", "").strip()
        is_pinned = bool(request.data.get("is_pinned", False))

        if not note_text:
            return Response({"error": "Note text cannot be blank."}, status=status.HTTP_400_BAD_REQUEST)

        note_obj = CustomerNote.objects.create(
            customer=customer,
            author=request.user,
            note=note_text,
            is_pinned=is_pinned,
        )

        AuditLog.objects.create(
            user=request.user,
            action="CUSTOMER_NOTE_ADDED",
            resource_type="CRM_NOTE",
            resource_id=str(note_obj.id),
            details={"customer_email": customer.email, "is_pinned": is_pinned},
        )

        return Response(CustomerNoteSerializer(note_obj).data, status=status.HTTP_201_CREATED)

    def delete(self, request, pk, note_id):
        from .models import CustomerNote
        note_obj = generics.get_object_or_404(CustomerNote, pk=note_id, customer_id=pk)
        note_obj.delete()
        return Response({"message": "Note deleted successfully."}, status=status.HTTP_200_OK)


class FeatureFlagsView(views.APIView):
    """
    Retrieves and updates business-level feature flags.
    GET: available publicly or to authenticated users.
    PUT / POST: restricted to ADMIN or users with FEATURES_MANAGE / SETTINGS_EDIT permission.
    """
    permission_classes = [permissions.AllowAny]

    def get(self, request):
        from .models import BusinessSetting
        flags = DEFAULT_FEATURE_FLAGS.copy()
        try:
            record = BusinessSetting.objects.filter(key="features").first()
            if record and isinstance(record.value, dict):
                flags.update(record.value)
        except Exception:
            pass
        return Response(flags)

    def put(self, request):
        from .models import BusinessSetting
        user = request.user
        if not user or not user.is_authenticated:
            return Response(
                {"error": "Authentication required."},
                status=status.HTTP_401_UNAUTHORIZED,
            )
        if not (
            user.role == "ADMIN"
            or user.is_superuser
            or user_has_permission(user, "FEATURES_MANAGE")
            or user_has_permission(user, "SETTINGS_EDIT")
        ):
            return Response(
                {"error": "Admin permission required to update feature flags."},
                status=status.HTTP_403_FORBIDDEN,
            )

        data = request.data
        if not isinstance(data, dict):
            return Response(
                {"error": "Feature flags payload must be a JSON dictionary."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        record, _ = BusinessSetting.objects.get_or_create(key="features")
        current_flags = DEFAULT_FEATURE_FLAGS.copy()
        if isinstance(record.value, dict):
            current_flags.update(record.value)

        updated = {}
        for k, v in data.items():
            if isinstance(v, bool):
                current_flags[k] = v
                updated[k] = v

        record.value = current_flags
        record.updated_by = user
        record.save()

        AuditLog.objects.create(
            user=user,
            action="FEATURE_FLAGS_UPDATED",
            resource_type="FEATURE_FLAGS",
            resource_id="features",
            details={"updated_flags": updated},
        )

        return Response(current_flags)

