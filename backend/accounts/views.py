import os
import uuid
import logging
import requests
from django.conf import settings
from django.utils import timezone
from django.db import models
from rest_framework import status, views, permissions, generics
from rest_framework.response import Response
from rest_framework_simplejwt.tokens import RefreshToken
from google.oauth2 import id_token
from google.auth.transport import requests as google_requests

from django.db.models import Q
from .models import User, CustomerProfile, StaffProfile, PasswordResetToken, PasswordResetOTP, LoginOTP
from .lookup_service import user_lookup_engine
from notifications.services import EmailNotificationService, SMSNotificationService
from .serializers import (
    UserSerializer,
    RegisterSerializer,
    LoginSerializer,
    GoogleAuthSerializer,
    B2BUserCreateSerializer,
    B2BUserUpdateSerializer,
    ForgotPasswordSerializer,
    ResetPasswordSerializer,
    CheckAvailabilitySerializer,
    RequestPasswordResetOTPSerializer,
    VerifyPasswordResetOTPSerializer,
    SetNewPasswordSerializer,
    RequestLoginOTPSerializer,
    VerifyLoginOTPSerializer,
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


logger = logging.getLogger(__name__)


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
        try:
            serializer = GoogleAuthSerializer(data=request.data)
            if not serializer.is_valid():
                return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

            credential = serializer.validated_data["credential"]

            # 1. Verify Google ID Token
            id_info = None
            from .settings_helper import BusinessSettingsHelper
            auth_config = BusinessSettingsHelper.get_section("auth")
            client_id = (auth_config.get("google_client_id") or "").strip() or getattr(settings, "GOOGLE_CLIENT_ID", "") or None

            # Primary: Verify ID token using google-auth library
            try:
                id_info = id_token.verify_oauth2_token(
                    credential, google_requests.Request(), client_id, clock_skew_in_seconds=10
                )
            except Exception as auth_err:
                logger.warning("Primary Google token verification notice: %s", auth_err)
                # If client_id check caused failure, try without audience check if audience was specified
                if client_id:
                    try:
                        id_info = id_token.verify_oauth2_token(
                            credential, google_requests.Request(), None, clock_skew_in_seconds=10
                        )
                    except Exception:
                        pass

                # Fallback to Google tokeninfo HTTP endpoint
                if not id_info:
                    try:
                        resp = requests.get(
                            f"https://oauth2.googleapis.com/tokeninfo?id_token={credential}",
                            timeout=8,
                        )
                        if resp.status_code == 200:
                            id_info = resp.json()
                    except Exception as http_err:
                        logger.warning("Fallback Google tokeninfo request error: %s", http_err)

            if not id_info or "email" not in id_info:
                return Response(
                    {
                        "code": "INVALID_GOOGLE_TOKEN",
                        "detail": "Failed to verify Google authentication token.",
                    },
                    status=status.HTTP_401_UNAUTHORIZED,
                )

            google_email = str(id_info.get("email", "")).strip().lower()
            google_id = str(id_info.get("sub", "")).strip() or None
            picture = id_info.get("picture") or ""
            name = id_info.get("name") or ""
            given_name = id_info.get("given_name") or ""
            family_name = id_info.get("family_name") or ""

            if not google_email:
                return Response(
                    {
                        "code": "INVALID_GOOGLE_TOKEN",
                        "detail": "Google authentication token does not contain a valid email address.",
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )

            # 2. Check if user already exists
            user = User.objects.filter(email__iexact=google_email).first()
            if not user and google_id:
                user = User.objects.filter(google_id=google_id).first()

            if user:
                # Existing user: Check account status
                if user.status in ("SUSPENDED", "DISABLED") or not user.is_active:
                    AuditLog.log(
                        user=user,
                        action="SUSPENDED_LOGIN_ATTEMPT",
                        resource_type="AUTH",
                        resource_id=user.email,
                        details={"status": user.status},
                        request=request,
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
                if google_id:
                    user.google_id = google_id
                if picture and not user.profile_image:
                    user.profile_image = str(picture)[:500]
                if not user.first_name and given_name:
                    user.first_name = str(given_name)[:100]
                if not user.last_name and family_name:
                    user.last_name = str(family_name)[:100]
                user.last_login_at = timezone.now()
                user.save()

                AuditLog.log(
                    user=user,
                    action="LOGIN_SUCCESS",
                    resource_type="AUTH",
                    resource_id=user.email,
                    details={"provider": "GOOGLE_OAUTH", "role": user.role},
                    request=request,
                )
            else:
                # 3. Auto-provision new customer account
                first_n = given_name or (name.split(" ")[0] if name else "Customer")
                last_n = family_name or (" ".join(name.split(" ")[1:]) if name and " " in name else "")
                user = User.objects.create_user(
                    email=google_email,
                    password=None,
                    first_name=str(first_n)[:100],
                    last_name=str(last_n)[:100],
                    profile_image=str(picture)[:500] if picture else "",
                    role="CUSTOMER",
                    status="ACTIVE",
                )
                if google_id:
                    user.google_id = google_id
                user.last_login_at = timezone.now()
                user.save()

                AuditLog.log(
                    user=user,
                    action="CUSTOMER_REGISTERED_GOOGLE",
                    resource_type="AUTH",
                    resource_id=user.email,
                    details={"provider": "GOOGLE_OAUTH", "role": "CUSTOMER"},
                    request=request,
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
        except Exception as e:
            logger.exception("Google authentication error: %s", e)
            return Response(
                {"detail": "Failed to complete Google authentication. Please try again."},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class CheckUserAvailabilityView(views.APIView):
    """
    High-Speed User Existence Check (Bitset / Bloom Filter + Indexed DB).
    Public endpoint with low latency for frontend debounced availability checks.
    """
    permission_classes = [permissions.AllowAny]

    def get(self, request):
        field = request.query_params.get("field", "").strip().lower()
        value = request.query_params.get("value", "").strip()

        serializer = CheckAvailabilitySerializer(data={"field": field, "value": value})
        if not serializer.is_valid():
            return Response(
                {"error": "Invalid field or format.", "details": serializer.errors},
                status=status.HTTP_400_BAD_REQUEST,
            )

        validated_field = serializer.validated_data["field"]
        validated_val = serializer.validated_data["value"]

        if validated_field == "email":
            exists, user = user_lookup_engine.check_email_exists(validated_val)
            masked = user_lookup_engine.mask_email(validated_val)
        else:
            exists, user = user_lookup_engine.check_phone_exists(validated_val)
            masked = user_lookup_engine.mask_phone(validated_val)

        is_claimable_guest = False
        if exists and user and user.is_unclaimed_guest():
            exists = False
            is_claimable_guest = True

        resp_data = {
            "exists": exists,
            "field": validated_field,
            "status": "REGISTERED" if exists else "AVAILABLE",
            "is_claimable_guest": is_claimable_guest,
            "masked_value": masked,
            "message": (
                f"An account is already registered with this {validated_field}."
                if exists
                else (
                    "Mobile number available. Registering will link your previous match bookings."
                    if is_claimable_guest and validated_field == "phone"
                    else (
                        "Email available. Registering will link your previous match bookings."
                        if is_claimable_guest
                        else f"This {validated_field} is available."
                    )
                )
            ),
        }
        if user and getattr(user, "phone", None):
            resp_data["masked_phone"] = user_lookup_engine.mask_phone(user.phone)
        if user and getattr(user, "email", None):
            resp_data["masked_email"] = user_lookup_engine.mask_email(user.email)

        return Response(resp_data, status=status.HTTP_200_OK)


class RequestPasswordResetOTPView(views.APIView):
    """
    Requests a 6-digit cryptographic OTP for password reset sent via Django SMTP.
    Enforces existence verification, cooldown timers, and single-use invalidation.
    """
    permission_classes = [permissions.AllowAny]

    def post(self, request):
        try:
            serializer = RequestPasswordResetOTPSerializer(data=request.data)
            if not serializer.is_valid():
                return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

            email = serializer.validated_data["email"]
            exists, user = user_lookup_engine.check_email_exists(email)

            if not exists or not user:
                return Response(
                    {
                        "error": "No player account found with this email address. Please check your spelling or register a new account.",
                        "status": "NOT_FOUND",
                    },
                    status=status.HTTP_404_NOT_FOUND,
                )

            if not user.is_active or user.status in ("SUSPENDED", "DISABLED"):
                return Response(
                    {
                        "error": "This account has been suspended or disabled. Please contact the arena help desk.",
                        "status": "INACTIVE",
                    },
                    status=status.HTTP_403_FORBIDDEN,
                )

            # Rate-limiting / Cooldown check: 60 seconds between OTP requests
            recent_otp = (
                PasswordResetOTP.objects.filter(user=user, is_used=False)
                .order_by("-created_at")
                .first()
            )
            now = timezone.now()
            if recent_otp and (now - recent_otp.created_at).total_seconds() < 60:
                remaining = 60 - int((now - recent_otp.created_at).total_seconds())
                return Response(
                    {
                        "error": f"Please wait {remaining} seconds before requesting a new OTP.",
                        "cooldown_seconds": remaining,
                    },
                    status=status.HTTP_429_TOO_MANY_REQUESTS,
                )

            # Extract client IP cleanly
            x_forwarded = request.META.get("HTTP_X_FORWARDED_FOR")
            ip_addr = x_forwarded.split(",")[0].strip() if x_forwarded else (request.META.get("REMOTE_ADDR") or "")
            user_agent = str(request.META.get("HTTP_USER_AGENT", ""))[:500]

            # Generate Cryptographic OTP
            otp_instance, raw_otp = PasswordResetOTP.generate_otp_for_user(
                user=user,
                validity_minutes=10,
                ip_address=ip_addr,
                user_agent=user_agent,
            )

            # Dispatch branded HTML email via Django SMTP
            email_sent = EmailNotificationService.send_otp_email(
                user=user,
                otp_code=raw_otp,
                valid_minutes=10,
                ip_address=ip_addr,
            )

            AuditLog.log(
                user=user,
                action="PASSWORD_RESET_OTP_REQUESTED",
                resource_type="AUTH",
                resource_id=user.email,
                ip_address=ip_addr,
                details={"email_delivered": email_sent},
                request=request,
            )

            response_payload = {
                "status": "OTP_SENT",
                "message": f"A 6-digit verification code has been sent to {user_lookup_engine.mask_email(user.email)}.",
                "email": user.email,
                "masked_email": user_lookup_engine.mask_email(user.email),
                "expires_in_seconds": 600,
                "cooldown_seconds": 60,
            }

            return Response(response_payload, status=status.HTTP_200_OK)
        except Exception as e:
            logger.exception("Error requesting password reset OTP: %s", e)
            return Response(
                {"error": "Failed to request password reset OTP. Please try again.", "detail": str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class VerifyPasswordResetOTPView(views.APIView):
    """
    Verifies the 6-digit OTP code submitted by the user.
    On success, returns a single-use signed reset_token (valid 15 mins).
    """
    permission_classes = [permissions.AllowAny]

    def post(self, request):
        try:
            serializer = VerifyPasswordResetOTPSerializer(data=request.data)
            if not serializer.is_valid():
                return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

            email = serializer.validated_data["email"]
            candidate_otp = serializer.validated_data["otp"]

            user = User.objects.filter(email__iexact=email).first()
            if not user:
                return Response(
                    {"error": "No account found for this email address."},
                    status=status.HTTP_404_NOT_FOUND,
                )

            active_otp = (
                PasswordResetOTP.objects.filter(
                    user=user, is_verified=False, is_used=False
                )
                .order_by("-created_at")
                .first()
            )

            if not active_otp:
                return Response(
                    {"error": "No active verification code found. Please request a new one."},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            if active_otp.is_expired():
                return Response(
                    {"error": "Verification code has expired. Please request a new OTP."},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            if active_otp.is_locked():
                return Response(
                    {
                        "error": "Maximum verification attempts exceeded. Please request a new OTP code.",
                        "status": "LOCKED",
                    },
                    status=status.HTTP_403_FORBIDDEN,
                )

            is_valid = active_otp.verify_code(candidate_otp)
            if not is_valid:
                remaining_attempts = max(0, active_otp.max_attempts - active_otp.attempts)
                return Response(
                    {
                        "error": f"Invalid verification code. {remaining_attempts} attempts remaining.",
                        "remaining_attempts": remaining_attempts,
                        "attempts_left": remaining_attempts,
                        "decision": "OUT",
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )

            # OTP Verified Successfully
            AuditLog.log(
                user=user,
                action="PASSWORD_RESET_OTP_VERIFIED",
                resource_type="AUTH",
                resource_id=user.email,
                request=request,
            )

            return Response(
                {
                    "status": "VERIFIED",
                    "decision": "SAFE",
                    "message": "OTP verified successfully. You may now set your new password.",
                    "reset_token": active_otp.reset_token,
                    "email": user.email,
                },
                status=status.HTTP_200_OK,
            )
        except Exception as e:
            logger.exception("Error verifying password reset OTP: %s", e)
            return Response(
                {"error": "Failed to verify OTP code. Please try again.", "detail": str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class ConfirmPasswordResetView(views.APIView):
    """
    Sets the new password using the verified reset_token.
    """
    permission_classes = [permissions.AllowAny]

    def post(self, request):
        try:
            serializer = SetNewPasswordSerializer(data=request.data)
            if not serializer.is_valid():
                return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

            reset_token = serializer.validated_data["reset_token"].strip()
            new_password = serializer.validated_data["password"]

            otp_record = PasswordResetOTP.objects.filter(
                reset_token=reset_token, is_verified=True, is_used=False
            ).first()

            if not otp_record or otp_record.is_expired():
                return Response(
                    {"error": "Reset session is invalid or has expired. Please request a new OTP."},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            user = otp_record.user
            user.set_password(new_password)
            user.save()

            # Invalidate OTP session token
            otp_record.is_used = True
            otp_record.save(update_fields=["is_used"])

            AuditLog.log(
                user=user,
                action="PASSWORD_RESET_COMPLETED",
                resource_type="AUTH",
                resource_id=user.email,
                request=request,
            )

            return Response(
                {
                    "status": "SUCCESS",
                    "message": "Your password has been successfully updated! You can now log in with your new credentials.",
                },
                status=status.HTTP_200_OK,
            )
        except Exception as e:
            logger.exception("Error confirming password reset: %s", e)
            return Response(
                {"error": "Failed to reset password. Please try again.", "detail": str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class ForgotPasswordView(views.APIView):
    """
    Legacy backwards-compatible endpoint (delegates to RequestPasswordResetOTPView).
    """
    permission_classes = [permissions.AllowAny]

    def post(self, request):
        return RequestPasswordResetOTPView().post(request)


class ResetPasswordView(views.APIView):
    """
    Legacy backwards-compatible token execution endpoint.
    """
    permission_classes = [permissions.AllowAny]

    def post(self, request):
        # If reset_token is provided, use ConfirmPasswordResetView
        if "reset_token" in request.data:
            return ConfirmPasswordResetView().post(request)

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

        AuditLog.log(
            user=user,
            action="PASSWORD_RESET_COMPLETED",
            resource_type="AUTH",
            resource_id=user.email,
            request=request,
        )

        return Response(
            {"message": "Your password has been reset successfully. You may now log in with your new password."},
            status=status.HTTP_200_OK,
        )

        return Response(
            {"message": "Your password has been reset successfully. You may now log in with your new password."},
            status=status.HTTP_200_OK,
        )


class RequestLoginOTPView(views.APIView):
    """
    Sends a 6-digit cryptographic OTP for mobile number or email login.
    Supports Dual-Identity Handshake:
    - If user exists with this phone: dispatches to their verified linked email.
    - If user is new: validates their provided email address, confirms no collision, and dispatches OTP.
    - If identifier is email: dispatches directly to email.
    Enforces 60-second cooldown rate-limiting and 10-minute expiry.
    """
    permission_classes = [permissions.AllowAny]

    def post(self, request):
        try:
            serializer = RequestLoginOTPSerializer(data=request.data)
            if not serializer.is_valid():
                return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

            identifier = serializer.validated_data["identifier"]
            ident_type = serializer.validated_data["identifier_type"]
            target_email = serializer.validated_data["target_email"]
            user = serializer.validated_data.get("resolved_user")
            is_new_user = serializer.validated_data.get("is_new_user", False)
            link_to_existing = serializer.validated_data.get("link_to_existing")

            # Check if user is suspended/disabled
            user_to_check = user or link_to_existing
            if user_to_check and (not user_to_check.is_active or user_to_check.status in ("SUSPENDED", "DISABLED")):
                return Response(
                    {
                        "error": "This account has been suspended or disabled. Please contact arena support.",
                        "status": "INACTIVE",
                    },
                    status=status.HTTP_403_FORBIDDEN,
                )

            # Cooldown check: 60 seconds between OTP requests for this identifier OR target_email
            recent_otp = (
                LoginOTP.objects.filter(
                    Q(identifier=identifier) | Q(identifier=target_email),
                    is_used=False,
                )
                .order_by("-created_at")
                .first()
            )
            now = timezone.now()
            if recent_otp and (now - recent_otp.created_at).total_seconds() < 60:
                remaining = 60 - int((now - recent_otp.created_at).total_seconds())
                return Response(
                    {
                        "error": f"Please wait {remaining} seconds before requesting a new passcode.",
                        "cooldown_seconds": remaining,
                    },
                    status=status.HTTP_429_TOO_MANY_REQUESTS,
                )

            # Client metadata
            x_forwarded = request.META.get("HTTP_X_FORWARDED_FOR")
            ip_addr = x_forwarded.split(",")[0].strip() if x_forwarded else (request.META.get("REMOTE_ADDR") or "")
            user_agent = str(request.META.get("HTTP_USER_AGENT", ""))[:500]

            # Invalidate any unverified OTPs for this target_email AND identifier
            LoginOTP.objects.filter(
                Q(identifier=target_email) | Q(identifier=identifier),
                is_used=False
            ).update(is_used=True)

            otp_instance, raw_otp = LoginOTP.generate_otp_for_identifier(
                identifier=target_email,
                channel="EMAIL",
                user=user_to_check,
                validity_minutes=10,
                ip_address=ip_addr,
                user_agent=user_agent,
            )

            # If requesting via phone, also index by phone so verify-otp can find the session regardless of parameters passed
            if ident_type == "phone" and identifier != target_email:
                LoginOTP.objects.create(
                    identifier=identifier,
                    channel="EMAIL",
                    user=user_to_check,
                    otp_hash=otp_instance.otp_hash,
                    expires_at=otp_instance.expires_at,
                    ip_address=ip_addr,
                    user_agent=user_agent,
                )

            # Dispatch branded HTML email via Django SMTP
            user_to_send = user_to_check or User(email=target_email, first_name="Player")
            EmailNotificationService.send_otp_email(
                user=user_to_send,
                otp_code=raw_otp,
                valid_minutes=10,
                ip_address=ip_addr,
            )

            logger.info("Login OTP generated for %s [target: %s]: %s", identifier, target_email, raw_otp)

            masked_email = user_lookup_engine.mask_email(target_email)
            masked_phone = user_lookup_engine.mask_phone(identifier) if ident_type == "phone" else ""

            AuditLog.log(
                user=user_to_check,
                action="LOGIN_OTP_REQUESTED",
                resource_type="AUTH",
                resource_id=identifier,
                details={"target_email": masked_email, "identifier_type": ident_type, "is_new_user": is_new_user},
                request=request,
            )

            resp_data = {
                "status": "OTP_SENT",
                "message": f"Verification passcode sent to {masked_email}.",
                "identifier": identifier,
                "identifier_type": ident_type,
                "target_email": target_email,
                "masked_email": masked_email,
                "masked_phone": masked_phone,
                "is_existing_player": not is_new_user,
                "cooldown_seconds": 60,
            }

            # In development or if DEBUG=True, provide dev_otp for rapid testing
            if settings.DEBUG or getattr(settings, "ENVIRONMENT", "") != "production":
                resp_data["dev_otp"] = raw_otp

            return Response(resp_data, status=status.HTTP_200_OK)

        except Exception as e:
            logger.exception("Error requesting login OTP: %s", e)
            return Response(
                {"error": "Failed to send verification passcode. Please try again.", "detail": str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class VerifyLoginOTPView(views.APIView):
    """
    Verifies 6-digit cryptographic OTP.
    Authenticates existing users or provisions new player accounts with verified phone & email.
    Issues JWT access and refresh tokens.
    """
    permission_classes = [permissions.AllowAny]

    def post(self, request):
        try:
            serializer = VerifyLoginOTPSerializer(data=request.data)
            if not serializer.is_valid():
                return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

            identifier = serializer.validated_data["identifier"]
            ident_type = serializer.validated_data["identifier_type"]
            candidate_otp = serializer.validated_data["otp"]
            provided_email = serializer.validated_data.get("email")
            first_name = serializer.validated_data.get("first_name", "Player")

            # Look up active OTP by email, identifier, or phone's linked account
            active_otp = None
            if provided_email:
                active_otp = (
                    LoginOTP.objects.filter(identifier=provided_email, is_used=False)
                    .order_by("-created_at")
                    .first()
                )

            if not active_otp:
                active_otp = (
                    LoginOTP.objects.filter(identifier=identifier, is_used=False)
                    .order_by("-created_at")
                    .first()
                )

            if not active_otp and ident_type == "phone":
                raw_10 = identifier[-10:] if len(identifier) >= 10 else identifier
                user_found = User.objects.filter(
                    Q(phone__endswith=raw_10) | Q(phone__iexact=identifier)
                ).first()
                if user_found:
                    active_otp = (
                        LoginOTP.objects.filter(identifier=user_found.email.lower(), is_used=False)
                        .order_by("-created_at")
                        .first()
                    )

            if not active_otp:
                return Response(
                    {"error": "No pending passcode found. Please request a new verification code."},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            if active_otp.is_expired():
                return Response(
                    {"error": "This passcode has expired. Please request a fresh code."},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            if active_otp.is_locked():
                return Response(
                    {"error": "Too many failed attempts. For your security, this code is locked. Please request a new one."},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            is_valid = active_otp.verify_code(candidate_otp)
            if not is_valid:
                attempts_left = max(0, active_otp.max_attempts - active_otp.attempts)
                return Response(
                    {
                        "error": f"Invalid verification code. {attempts_left} attempts remaining.",
                        "attempts_left": attempts_left,
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )

            # OTP successfully verified -> mark used
            active_otp.is_used = True
            active_otp.save(update_fields=["is_used"])
            # Invalidate all linked OTP records with this hash
            LoginOTP.objects.filter(otp_hash=active_otp.otp_hash, is_used=False).update(is_used=True)

            # Resolve user or auto-provision
            user = None
            if ident_type == "email":
                user = User.objects.filter(email__iexact=identifier).first()
            else:
                raw_10 = identifier[-10:] if len(identifier) >= 10 else identifier
                user = User.objects.filter(
                    Q(phone__endswith=raw_10) | Q(phone__iexact=identifier)
                ).first()

            # If user not found by phone, check if active_otp.identifier is an email
            if not user and "@" in active_otp.identifier:
                user = User.objects.filter(email__iexact=active_otp.identifier).first()

            if not user:
                # Provision new player account with verified mobile + email
                final_email = provided_email or (active_otp.identifier if "@" in active_otp.identifier else "")
                if not final_email:
                    linked_email_otp = LoginOTP.objects.filter(otp_hash=active_otp.otp_hash, identifier__contains="@").first()
                    if linked_email_otp:
                        final_email = linked_email_otp.identifier
                if not final_email:
                    final_email = f"player_{identifier[-10:]}@friendsturf.com"

                user = User.objects.create_user(
                    email=final_email,
                    password=None,
                    first_name=first_name or "Player",
                    last_name="",
                    phone=identifier if ident_type == "phone" else "",
                    role="CUSTOMER",
                    status="ACTIVE",
                )

                # Add to lookup bloom filter
                user_lookup_engine.add_to_filter(user.email)
                if user.phone:
                    user_lookup_engine.add_to_filter(user.phone)

                AuditLog.log(
                    user=user,
                    action="CUSTOMER_REGISTERED_OTP",
                    resource_type="AUTH",
                    resource_id=identifier,
                    details={"phone": user.phone, "email": user.email, "role": "CUSTOMER"},
                    request=request,
                )
            else:
                # User exists: link phone if they didn't have one
                if ident_type == "phone" and not user.phone:
                    user.phone = identifier
                    user_lookup_engine.add_to_filter(user.phone)
                # Link email if existing user didn't have one or had a placeholder / guest email
                if provided_email and (
                    not user.email
                    or user.email.endswith("@friendsturf.com")
                    or user.email.endswith("@friendsturf.local")
                    or user.email.startswith("guest_")
                    or user.email.startswith("walkin_")
                    or user.email.startswith("player_")
                ):
                    user.email = provided_email
                    user_lookup_engine.add_to_filter(user.email)

            if user.status in ("SUSPENDED", "DISABLED") or not user.is_active:
                return Response(
                    {
                        "error": "This account has been suspended or disabled. Please contact the arena desk.",
                        "status": "INACTIVE",
                    },
                    status=status.HTTP_403_FORBIDDEN,
                )

            # If user was INVITED, activate them
            if user.status == "INVITED":
                user.status = "ACTIVE"

            user.last_login_at = timezone.now()
            user.save()

            AuditLog.log(
                user=user,
                action="LOGIN_SUCCESS_OTP",
                resource_type="AUTH",
                resource_id=identifier,
                details={"identifier_type": ident_type, "role": user.role},
                request=request,
            )

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

        except Exception as e:
            logger.exception("Error verifying login OTP: %s", e)
            return Response(
                {"error": "Failed to complete login verification. Please try again.", "detail": str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
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
        user = User.objects.select_related("customer_profile", "staff_profile").filter(pk=request.user.pk).first() or request.user
        serializer = UserSerializer(user)
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
        # Include all CUSTOMER accounts and any user account that has booking history
        customers = (
            User.objects.filter(models.Q(role="CUSTOMER") | models.Q(bookings__isnull=False))
            .distinct()
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
        # Ensure customer profiles exist and are synced
        for c in customers:
            if not hasattr(c, "customer_profile") or c.customer_profile is None or (c.bookings.count() > 0 and c.customer_profile.total_bookings == 0):
                CustomerProfile.sync_for_user(c)

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
            email = data["email"]
            first_name = data.get("first_name", "").strip()
            if not first_name:
                first_name = email.split("@")[0].capitalize()
            last_name = data.get("last_name", "").strip()
            phone = data.get("phone", "").strip()
            role = data.get("role", "STAFF")
            b2b_status = data.get("status", "INVITED")
            department = data.get("department", "").strip() or "Turf Operations"
            employee_id = data.get("employee_id", "").strip()

            existing_user = User.objects.filter(email__iexact=email).first()
            if existing_user:
                existing_user.role = role
                existing_user.status = b2b_status
                if first_name and not existing_user.first_name:
                    existing_user.first_name = first_name
                if last_name and not existing_user.last_name:
                    existing_user.last_name = last_name
                if phone and not existing_user.phone:
                    existing_user.phone = phone
                existing_user.save()
                user = existing_user
            else:
                user = User.objects.create_user(
                    email=email,
                    password=None,
                    first_name=first_name,
                    last_name=last_name,
                    phone=phone,
                    role=role,
                    status=b2b_status,
                )

            # Update staff profile details
            staff_prof, _ = StaffProfile.objects.get_or_create(user=user)
            if employee_id:
                staff_prof.employee_id = employee_id
            staff_prof.department = department
            staff_prof.save()

            AuditLog.objects.create(
                user=request.user,
                action="B2B_USER_INVITED",
                resource_type="USER",
                resource_id=user.email,
                details={"role": user.role, "status": user.status, "department": department},
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

        # Permanent Superadmin Protection
        if user.is_permanent_admin():
            if "role" in data and data["role"] != "ADMIN":
                return Response(
                    {"error": f"The permanent root admin account '{user.email}' cannot be demoted."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            if "status" in data and data["status"] != "ACTIVE":
                return Response(
                    {"error": f"The permanent root admin account '{user.email}' cannot be suspended or deactivated."},
                    status=status.HTTP_400_BAD_REQUEST,
                )

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

    def delete(self, request, pk):
        user = generics.get_object_or_404(User, pk=pk)
        if user.is_permanent_admin():
            return Response(
                {"error": f"The permanent root admin account '{user.email}' is protected and cannot be deleted."},
                status=status.HTTP_403_FORBIDDEN,
            )

        if not (
            request.user.role == "ADMIN"
            or request.user.is_superuser
            or user_has_permission(request.user, "STAFF_EDIT")
        ):
            return Response(
                {"error": "Permission denied: STAFF_EDIT required to remove staff members."},
                status=status.HTTP_403_FORBIDDEN,
            )

        user_email = user.email
        user.delete()
        AuditLog.objects.create(
            user=request.user,
            action="B2B_USER_DELETED",
            resource_type="USER",
            resource_id=user_email,
            details={"deleted_email": user_email},
        )
        return Response({"message": f"User {user_email} deleted successfully."}, status=status.HTTP_200_OK)


class BusinessSettingsView(views.APIView):
    """
    Persists and retrieves platform business settings:
    Company info, booking rules, operating hours, payment config, turnstile rules, and reminders.
    """
    def get_permissions(self):
        if self.request.method == "GET":
            return [permissions.AllowAny()]
        return [permissions.IsAuthenticated()]

    def get(self, request):
        from .models import BusinessSetting
        from .settings_helper import BusinessSettingsHelper, DEFAULT_BUSINESS_SETTINGS
        settings_dict = {}
        for section in DEFAULT_BUSINESS_SETTINGS.keys():
            settings_dict[section] = BusinessSettingsHelper.get_section(section)

        # Sanitize payments: never expose private API secrets to the client
        if "payments" in settings_dict and isinstance(settings_dict["payments"], dict):
            payments_sanitized = dict(settings_dict["payments"])
            for secret_key in ("keySecret", "testKeySecret", "liveKeySecret"):
                if secret_key in payments_sanitized:
                    payments_sanitized[secret_key] = "••••••••" if payments_sanitized[secret_key] else ""
            settings_dict["payments"] = payments_sanitized

        # Include feature flags in response
        features_record = BusinessSetting.objects.filter(key="features").first()
        settings_dict["features"] = (
            features_record.value if features_record and isinstance(features_record.value, dict) else DEFAULT_FEATURE_FLAGS
        )
        return Response(settings_dict)

    def post(self, request):
        if not (request.user.role == "ADMIN" or request.user.is_superuser):
            return Response({"error": "Admin permission required."}, status=status.HTTP_403_FORBIDDEN)

        from .models import BusinessSetting
        from .settings_helper import BusinessSettingsHelper
        data = request.data
        updated_sections = []

        for section, values in data.items():
            if isinstance(values, dict):
                record, _ = BusinessSetting.objects.get_or_create(key=section)
                record.value = values
                record.updated_by = request.user
                record.save()
                updated_sections.append(section)

        # Invalidate cached settings
        BusinessSettingsHelper.invalidate_cache()

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
        from decimal import Decimal

        customer = generics.get_object_or_404(User, pk=pk)
        CustomerProfile.sync_for_user(customer)
        user_data = UserSerializer(customer).data

        # ── Live queries for detail sections ──────────────────────────────────
        bookings = (
            Booking.objects.filter(customer=customer)
            .select_related("turf")
            .order_by("-date", "-created_at")
        )
        payments = (
            Payment.objects.filter(customer=customer)
            .select_related("booking")
            .order_by("-created_at")
        )
        reviews = Review.objects.filter(customer=customer).select_related("turf").order_by("-created_at")
        notes = CustomerNote.objects.filter(customer=customer).select_related("author").order_by("-is_pinned", "-created_at")

        # ── Canonical summary metrics from CustomerProfile ────────────────────
        # CustomerProfile.total_bookings / total_spending are the authoritative
        # denormalized counters — the same source the customer list table reads.
        # Using them here ensures the CRM modal and the table always agree.
        try:
            prof = customer.customer_profile
            profile_total_bookings = int(prof.total_bookings or 0)
            profile_total_spending = float(prof.total_spending or 0)
            wallet_balance = float(prof.wallet_balance or 0)
            cancellation_count = int(prof.cancellation_count or 0)
            no_show_count = int(prof.no_show_count or 0)
        except Exception:
            prof = None
            profile_total_bookings = 0
            profile_total_spending = 0.0
            wallet_balance = 0.0
            cancellation_count = 0
            no_show_count = 0

        # Live booking-table counts (for sub-stats only; may differ from counter
        # if a booking was hard-deleted or the counter drifted via seed data)
        live_booking_count = bookings.count()
        completed_bookings_count = bookings.filter(status="COMPLETED").count()
        cancelled_bookings_count = bookings.filter(status="CANCELLED").count()

        # Use profile counter for the headline number; live count for sub-stats
        repeat_rate = 0.0
        if profile_total_bookings > 1:
            repeat_rate = round((profile_total_bookings - 1) / profile_total_bookings * 100, 1)

        serialized_bookings = BookingSerializer(bookings[:30], many=True).data

        return Response({
            "customer": user_data,
            "metrics": {
                # Profile counters — consistent with the customer list table
                "total_bookings": profile_total_bookings,
                "total_spent": profile_total_spending,
                "wallet_balance": wallet_balance,
                "cancellation_count": cancellation_count,
                "no_show_count": no_show_count,
                "repeat_rate": repeat_rate,
                # Live sub-stats from Booking table
                "completed_bookings": completed_bookings_count,
                "cancelled_bookings": cancelled_bookings_count,
                "live_booking_count": live_booking_count,
            },
            # 'recent_bookings' key used by the admin frontend CRM modal
            "recent_bookings": serialized_bookings[:10],
            # 'bookings' kept for backward compatibility
            "bookings": serialized_bookings,
            "payments": PaymentSerializer(payments[:30], many=True).data,
            "reviews": ReviewSerializer(reviews, many=True).data,
            "notes": CustomerNoteSerializer(notes, many=True).data,
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

        from .settings_helper import BusinessSettingsHelper
        BusinessSettingsHelper.invalidate_cache("features")

        AuditLog.objects.create(
            user=user,
            action="FEATURE_FLAGS_UPDATED",
            resource_type="FEATURE_FLAGS",
            resource_id="features",
            details={"updated_flags": updated},
        )

        return Response(current_flags)

