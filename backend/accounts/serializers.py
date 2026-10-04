import re
from typing import Tuple
from rest_framework import serializers
from django.contrib.auth import authenticate
from django.db.models import Q
from .models import User, CustomerProfile, StaffProfile, LoginOTP


def validate_password_complexity(password: str) -> str:
    """Enforces enterprise password strength policy."""
    if len(password) < 8:
        raise serializers.ValidationError("Password must be at least 8 characters long.")
    if not re.search(r"[A-Z]", password):
        raise serializers.ValidationError("Password must contain at least one uppercase letter (A-Z).")
    if not re.search(r"[a-z]", password):
        raise serializers.ValidationError("Password must contain at least one lowercase letter (a-z).")
    if not re.search(r"\d", password):
        raise serializers.ValidationError("Password must contain at least one numeric digit (0-9).")
    if not re.search(r"[!@#$%^&*()_+\-=\[\]{}|;:,.<>?]", password):
        raise serializers.ValidationError("Password must contain at least one special character (!@#$%^&*...).")
    return password


def validate_indian_phone_number(phone: str) -> str:
    """Validates and standardizes Indian mobile numbers."""
    if not phone:
        return ""
    clean = re.sub(r"[^\d+]", "", str(phone).strip())
    raw_10 = clean[-10:] if len(clean) >= 10 else clean
    if not re.match(r"^[6-9]\d{9}$", raw_10):
        raise serializers.ValidationError("Please enter a valid 10-digit Indian mobile number starting with 6, 7, 8, or 9.")
    return f"+91{raw_10}"


def normalize_auth_identifier(identifier: str) -> Tuple[str, str]:
    """
    Validates and standardizes an authentication identifier (email or Indian mobile number).
    Returns (normalized_value, identifier_type) where identifier_type is 'email' or 'phone'.
    """
    raw = (identifier or "").strip()
    if not raw:
        raise serializers.ValidationError("Please enter your mobile number or email address.")
    
    if "@" in raw:
        email_regex = r"^[^@\s]+@[^@\s]+\.[^@\s]+$"
        if not re.match(email_regex, raw):
            raise serializers.ValidationError("Please enter a valid email address.")
        return User.canonicalize_email(raw), "email"
    else:
        # Validate as Indian mobile number
        formatted = validate_indian_phone_number(raw)
        return formatted, "phone"


class RequestLoginOTPSerializer(serializers.Serializer):
    identifier = serializers.CharField(required=True)
    email = serializers.EmailField(required=False, allow_blank=True)
    channel = serializers.ChoiceField(choices=["SMS", "EMAIL"], required=False, default="EMAIL")

    def validate(self, data):
        ident = data.get("identifier", "").strip()
        canonical, ident_type = normalize_auth_identifier(ident)
        data["identifier"] = canonical
        data["identifier_type"] = ident_type

        provided_email = (data.get("email") or "").strip().lower()

        if ident_type == "phone":
            # Lookup user by phone in database
            raw_10 = canonical[-10:] if len(canonical) >= 10 else canonical
            user = User.objects.filter(
                Q(phone__endswith=raw_10) | Q(phone__iexact=canonical)
            ).first()

            if user:
                # Check if this is an unclaimed guest with a dummy email
                is_unclaimed_placeholder = (
                    user.is_unclaimed_guest()
                    and (
                        user.email.endswith("@friendsturf.local")
                        or user.email.endswith("@friendsturf.com")
                        or user.email.startswith("guest_")
                        or user.email.startswith("walkin_")
                        or user.email.startswith("player_")
                    )
                )

                if is_unclaimed_placeholder:
                    if not provided_email:
                        raise serializers.ValidationError({
                            "email": "Please provide your email address to receive your login passcode and link your match bookings."
                        })
                    existing_email_user = User.objects.filter(email__iexact=provided_email).first()
                    if existing_email_user and existing_email_user.id != user.id and not existing_email_user.is_unclaimed_guest():
                        raise serializers.ValidationError({
                            "email": "An account with this email is already registered. Please sign in with that email."
                        })
                    data["resolved_user"] = user
                    data["target_email"] = provided_email
                    data["is_new_user"] = False
                    data["is_guest_upgrade"] = True
                else:
                    registered_email = User.canonicalize_email(user.email)
                    if provided_email and provided_email != registered_email:
                        raise serializers.ValidationError({
                            "email": "The provided email does not match the registered account for this mobile number."
                        })
                    data["resolved_user"] = user
                    data["target_email"] = registered_email
                    data["is_new_user"] = False
            else:
                # New user registering with this phone
                if not provided_email:
                    raise serializers.ValidationError({
                        "email": "Email address is required for new player registration."
                    })
                # Check cross-identity collision: is this email already taken by someone else?
                existing_email_user = User.objects.filter(email__iexact=provided_email).first()
                if existing_email_user:
                    # Check if this existing user already has a different phone number
                    clean_existing_phone = re.sub(r"[^\d+]", "", existing_email_user.phone or "")
                    if clean_existing_phone and clean_existing_phone[-10:] != raw_10:
                        raise serializers.ValidationError({
                            "email": "An account with this email is already registered with a different mobile number. Please sign in with that number or email."
                        })
                    data["link_to_existing"] = existing_email_user

                data["target_email"] = provided_email
                data["is_new_user"] = True
                data["resolved_user"] = None
        else:
            # Identifier is email directly
            data["target_email"] = canonical
            data["resolved_user"] = User.objects.filter(email__iexact=canonical).first()
            data["is_new_user"] = not bool(data["resolved_user"])

        data["channel"] = "EMAIL"
        return data


class VerifyLoginOTPSerializer(serializers.Serializer):
    identifier = serializers.CharField(required=True)
    otp = serializers.CharField(min_length=6, max_length=6, required=True)
    email = serializers.EmailField(required=False, allow_blank=True)
    first_name = serializers.CharField(required=False, allow_blank=True, default="")

    def validate_otp(self, value):
        clean = (value or "").strip()
        if not re.match(r"^\d{6}$", clean):
            raise serializers.ValidationError("OTP must be exactly 6 numeric digits.")
        return clean

    def validate(self, data):
        ident = data.get("identifier", "").strip()
        canonical, ident_type = normalize_auth_identifier(ident)
        data["identifier"] = canonical
        data["identifier_type"] = ident_type
        if data.get("email"):
            data["email"] = data["email"].strip().lower()
        return data


class CheckAvailabilitySerializer(serializers.Serializer):
    field = serializers.ChoiceField(choices=["email", "phone"])
    value = serializers.CharField(max_length=255)

    def validate(self, data):
        f = data["field"]
        v = data["value"].strip()
        if f == "email":
            email_regex = r"^[^@\s]+@[^@\s]+\.[^@\s]+$"
            if not re.match(email_regex, v):
                raise serializers.ValidationError({"value": "Please enter a valid email format."})
            data["value"] = v.lower()
        elif f == "phone":
            data["value"] = validate_indian_phone_number(v)
        return data


class RegisterSerializer(serializers.Serializer):
    email = serializers.EmailField()
    password = serializers.CharField(write_only=True)
    first_name = serializers.CharField(max_length=100)
    last_name = serializers.CharField(max_length=100, required=False, allow_blank=True)
    phone = serializers.CharField(max_length=20, required=False, allow_blank=True)

    def validate_email(self, value):
        canonical = User.canonicalize_email(value)
        existing = User.objects.filter(email__iexact=canonical).first()
        if existing and not existing.is_unclaimed_guest():
            raise serializers.ValidationError(
                "An account with this email address already exists. Please Sign In."
            )
        return canonical

    def validate_phone(self, value):
        if not value:
            return ""
        formatted = validate_indian_phone_number(value)
        raw_10 = formatted[-10:]
        existing = User.objects.filter(
            Q(phone__endswith=raw_10) | Q(phone__iexact=formatted)
        ).first()
        if existing and not existing.is_unclaimed_guest():
            raise serializers.ValidationError(
                "An account with this mobile number already exists. Please Sign In."
            )
        return formatted

    def validate_password(self, value):
        return validate_password_complexity(value)

    def create(self, validated_data):
        email = validated_data["email"]
        phone = validated_data.get("phone", "")
        raw_10 = phone[-10:] if phone and len(phone) >= 10 else phone

        existing_user = None
        if email:
            existing_user = User.objects.filter(email__iexact=email).first()
        if not existing_user and raw_10:
            existing_user = User.objects.filter(
                Q(phone__endswith=raw_10) | Q(phone__iexact=phone)
            ).first()

        # Merge secondary guest user if separate record existed by phone
        if raw_10:
            phone_user = User.objects.filter(
                Q(phone__endswith=raw_10) | Q(phone__iexact=phone)
            ).first()
            if phone_user and existing_user and phone_user.id != existing_user.id and phone_user.is_unclaimed_guest():
                from bookings.models import Booking
                from payments.models import Payment
                Booking.objects.filter(customer=phone_user).update(customer=existing_user)
                Payment.objects.filter(customer=phone_user).update(customer=existing_user)
                phone_user.delete()

        if existing_user and existing_user.is_unclaimed_guest():
            # Seamless claim/upgrade: preserve past bookings and payments
            existing_user.email = email
            existing_user.set_password(validated_data["password"])
            existing_user.first_name = validated_data.get("first_name", "") or existing_user.first_name
            existing_user.last_name = validated_data.get("last_name", "") or existing_user.last_name
            if phone:
                existing_user.phone = phone
            existing_user.role = "CUSTOMER"
            existing_user.status = "ACTIVE"
            existing_user.save()

            CustomerProfile.objects.get_or_create(user=existing_user)

            from .lookup_service import user_lookup_engine
            user_lookup_engine.add_to_filter(existing_user.email)
            if existing_user.phone:
                user_lookup_engine.add_to_filter(existing_user.phone)

            return existing_user

        user = User.objects.create_user(
            email=email,
            password=validated_data["password"],
            first_name=validated_data.get("first_name", ""),
            last_name=validated_data.get("last_name", ""),
            phone=phone,
            role="CUSTOMER",
            status="ACTIVE",
        )

        # Synchronize new user into in-memory Bloom filter bitset
        from .lookup_service import user_lookup_engine
        user_lookup_engine.add_to_filter(user.email)
        if user.phone:
            user_lookup_engine.add_to_filter(user.phone)

        return user


class RequestPasswordResetOTPSerializer(serializers.Serializer):
    email = serializers.EmailField()

    def validate_email(self, value):
        return User.canonicalize_email(value)


class VerifyPasswordResetOTPSerializer(serializers.Serializer):
    email = serializers.EmailField()
    otp = serializers.CharField(min_length=6, max_length=6)

    def validate_email(self, value):
        return User.canonicalize_email(value)

    def validate_otp(self, value):
        clean = value.strip()
        if not re.match(r"^\d{6}$", clean):
            raise serializers.ValidationError("OTP must be exactly 6 numeric digits.")
        return clean


class SetNewPasswordSerializer(serializers.Serializer):
    reset_token = serializers.CharField(required=True)
    password = serializers.CharField(write_only=True, required=False)
    new_password = serializers.CharField(write_only=True, required=False)
    confirm_password = serializers.CharField(write_only=True, required=True)

    def validate(self, data):
        pwd = data.get("new_password") or data.get("password")
        if not pwd:
            raise serializers.ValidationError({"password": "Password is required."})
        validate_password_complexity(pwd)
        if pwd != data.get("confirm_password"):
            raise serializers.ValidationError({"confirm_password": "Passwords do not match."})
        data["password"] = pwd
        return data


class ForgotPasswordSerializer(serializers.Serializer):
    email = serializers.EmailField()

    def validate_email(self, value):
        return value.strip().lower()


class ResetPasswordSerializer(serializers.Serializer):
    token = serializers.CharField(required=True)
    password = serializers.CharField(write_only=True, min_length=8)
    confirm_password = serializers.CharField(write_only=True, min_length=8)

    def validate(self, data):
        if data.get("password") != data.get("confirm_password"):
            raise serializers.ValidationError({"confirm_password": "Passwords do not match."})
        return data



class CustomerProfileSerializer(serializers.ModelSerializer):
    class Meta:
        model = CustomerProfile
        fields = [
            "wallet_balance",
            "membership_tier",
            "total_bookings",
            "total_spending",
            "cancellation_count",
            "no_show_count",
            "birthday",
        ]


class StaffProfileSerializer(serializers.ModelSerializer):
    class Meta:
        model = StaffProfile
        fields = [
            "employee_id",
            "department",
            "is_on_duty",
        ]


from .permissions import get_user_permissions


class UserSerializer(serializers.ModelSerializer):
    customer_profile = CustomerProfileSerializer(read_only=True)
    staff_profile = StaffProfileSerializer(read_only=True)
    full_name = serializers.ReadOnlyField()
    permissions = serializers.SerializerMethodField()
    is_permanent = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = [
            "id",
            "email",
            "google_id",
            "first_name",
            "last_name",
            "full_name",
            "profile_image",
            "phone",
            "role",
            "status",
            "permissions",
            "is_permanent",
            "date_joined",
            "last_login_at",
            "customer_profile",
            "staff_profile",
        ]
        read_only_fields = ["id", "google_id", "date_joined", "last_login_at", "role", "status", "permissions", "is_permanent"]

    def get_permissions(self, obj):
        return sorted(list(get_user_permissions(obj)))

    def get_is_permanent(self, obj):
        return getattr(obj, "is_permanent_admin", lambda: False)()


class AdminCustomerCreateSerializer(serializers.Serializer):
    email = serializers.EmailField()
    password = serializers.CharField(write_only=True, min_length=6)
    first_name = serializers.CharField(max_length=100)
    last_name = serializers.CharField(max_length=100, required=False, allow_blank=True)
    phone = serializers.CharField(max_length=20, required=False, allow_blank=True)

    def validate_email(self, value):
        if User.objects.filter(email__iexact=value).exists():
            raise serializers.ValidationError(
                "An account with this email already exists."
            )
        return value.lower()

    def create(self, validated_data):
        user = User.objects.create_user(
            email=validated_data["email"],
            password=validated_data["password"],
            first_name=validated_data.get("first_name", ""),
            last_name=validated_data.get("last_name", ""),
            phone=validated_data.get("phone", ""),
            role="CUSTOMER",
            status="ACTIVE",
        )
        return user


class LoginSerializer(serializers.Serializer):
    email = serializers.CharField(required=False, allow_blank=True)
    identifier = serializers.CharField(required=False, allow_blank=True)
    password = serializers.CharField(write_only=True)

    def validate(self, data):
        raw_id = (data.get("identifier") or data.get("email") or "").strip()
        password = data.get("password", "")
        if not raw_id or not password:
            raise serializers.ValidationError("Mobile number or email and password are required.")

        user = None
        if "@" in raw_id:
            email_canonical = User.canonicalize_email(raw_id)
            user = authenticate(username=email_canonical, password=password)
            if not user:
                found_user = User.objects.filter(email__iexact=email_canonical).first()
                if found_user and found_user.check_password(password):
                    user = found_user
        else:
            # Indian mobile number lookup
            try:
                formatted_phone = validate_indian_phone_number(raw_id)
                raw_10 = formatted_phone[-10:]
            except serializers.ValidationError:
                raw_10 = re.sub(r"\D", "", raw_id)[-10:]
                formatted_phone = f"+91{raw_10}" if len(raw_10) == 10 else raw_id

            found_user = User.objects.filter(
                Q(phone__endswith=raw_10) | Q(phone__iexact=formatted_phone) | Q(phone__iexact=raw_id)
            ).first()
            if found_user and found_user.check_password(password):
                user = found_user

        if not user:
            raise serializers.ValidationError("Invalid credentials. Please verify your mobile number/email and password.")
        if not user.is_active or user.status in ("SUSPENDED", "DISABLED"):
            raise serializers.ValidationError("This account has been suspended or disabled.")
        data["user"] = user
        return data


class GoogleAuthSerializer(serializers.Serializer):
    credential = serializers.CharField(required=True)


class B2BUserCreateSerializer(serializers.Serializer):
    email = serializers.EmailField()
    first_name = serializers.CharField(max_length=100, required=False, allow_blank=True, default="")
    last_name = serializers.CharField(max_length=100, required=False, allow_blank=True, default="")
    phone = serializers.CharField(max_length=20, required=False, allow_blank=True, default="")
    role = serializers.ChoiceField(choices=["STAFF", "ADMIN"], default="STAFF")
    status = serializers.ChoiceField(choices=["ACTIVE", "INVITED"], default="INVITED")
    department = serializers.CharField(max_length=100, required=False, allow_blank=True, default="Turf Operations")
    employee_id = serializers.CharField(max_length=50, required=False, allow_blank=True, default="")

    def validate_email(self, value):
        cleaned = value.strip().lower()
        existing = User.objects.filter(email__iexact=cleaned).first()
        if existing and existing.role in ["STAFF", "ADMIN"]:
            raise serializers.ValidationError("A team member with this email already exists.")
        return cleaned


class B2BUserUpdateSerializer(serializers.Serializer):
    first_name = serializers.CharField(max_length=100, required=False)
    last_name = serializers.CharField(max_length=100, required=False, allow_blank=True)
    phone = serializers.CharField(max_length=20, required=False, allow_blank=True)
    role = serializers.ChoiceField(choices=["STAFF", "ADMIN"], required=False)
    status = serializers.ChoiceField(choices=["ACTIVE", "INVITED", "SUSPENDED", "DISABLED"], required=False)
    department = serializers.CharField(max_length=100, required=False)
    employee_id = serializers.CharField(max_length=50, required=False, allow_blank=True)
    is_on_duty = serializers.BooleanField(required=False)


class ForgotPasswordSerializer(serializers.Serializer):
    email = serializers.EmailField()

    def validate_email(self, value):
        return value.strip().lower()


class ResetPasswordSerializer(serializers.Serializer):
    token = serializers.CharField(required=True)
    password = serializers.CharField(write_only=True, min_length=6)
    confirm_password = serializers.CharField(write_only=True, min_length=6)

    def validate(self, data):
        if data.get("password") != data.get("confirm_password"):
            raise serializers.ValidationError({"confirm_password": "Passwords do not match."})
        return data


class CustomerNoteSerializer(serializers.ModelSerializer):
    author_name = serializers.SerializerMethodField()

    class Meta:
        from .models import CustomerNote
        model = CustomerNote
        fields = ["id", "customer", "author", "author_name", "note", "is_pinned", "created_at", "updated_at"]
        read_only_fields = ["id", "author", "author_name", "created_at", "updated_at"]

    def get_author_name(self, obj):
        return obj.author.get_full_name() if obj.author else "System"

