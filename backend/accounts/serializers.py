import re
from rest_framework import serializers
from django.contrib.auth import authenticate
from .models import User, CustomerProfile, StaffProfile


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
    referral_code = serializers.CharField(
        max_length=20, required=False, allow_blank=True
    )

    def validate_email(self, value):
        canonical = User.canonicalize_email(value)
        if User.objects.filter(email__iexact=canonical).exists():
            raise serializers.ValidationError(
                "An account with this email address already exists. Please Sign In."
            )
        return canonical

    def validate_phone(self, value):
        if not value:
            return ""
        formatted = validate_indian_phone_number(value)
        raw_10 = formatted[-10:]
        if User.objects.filter(phone__endswith=raw_10).exists():
            raise serializers.ValidationError(
                "An account with this mobile number already exists. Please Sign In."
            )
        return formatted

    def validate_password(self, value):
        return validate_password_complexity(value)

    def create(self, validated_data):
        ref_code = validated_data.pop("referral_code", None)
        referred_by = None
        if ref_code:
            referred_by = User.objects.filter(
                referral_code=ref_code.strip().upper()
            ).first()

        user = User.objects.create_user(
            email=validated_data["email"],
            password=validated_data["password"],
            first_name=validated_data.get("first_name", ""),
            last_name=validated_data.get("last_name", ""),
            phone=validated_data.get("phone", ""),
            role="CUSTOMER",
            status="ACTIVE",
            referred_by=referred_by,
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
            "loyalty_points",
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
            "referral_code",
            "date_joined",
            "last_login_at",
            "customer_profile",
            "staff_profile",
        ]
        read_only_fields = ["id", "google_id", "referral_code", "date_joined", "last_login_at", "role", "status", "permissions"]

    def get_permissions(self, obj):
        return sorted(list(get_user_permissions(obj)))


class RegisterSerializer(serializers.Serializer):
    email = serializers.EmailField()
    password = serializers.CharField(write_only=True, min_length=6)
    first_name = serializers.CharField(max_length=100)
    last_name = serializers.CharField(max_length=100, required=False, allow_blank=True)
    phone = serializers.CharField(max_length=20, required=False, allow_blank=True)
    referral_code = serializers.CharField(
        max_length=20, required=False, allow_blank=True
    )

    def validate_email(self, value):
        if User.objects.filter(email__iexact=value).exists():
            raise serializers.ValidationError(
                "An account with this email already exists."
            )
        return value.lower()

    def create(self, validated_data):
        ref_code = validated_data.pop("referral_code", None)
        referred_by = None
        if ref_code:
            referred_by = User.objects.filter(
                referral_code=ref_code.strip().upper()
            ).first()

        # Role self-selection is strictly prevented: all registrations are CUSTOMER
        user = User.objects.create_user(
            email=validated_data["email"],
            password=validated_data["password"],
            first_name=validated_data.get("first_name", ""),
            last_name=validated_data.get("last_name", ""),
            phone=validated_data.get("phone", ""),
            role="CUSTOMER",
            status="ACTIVE",
            referred_by=referred_by,
        )
        return user


class LoginSerializer(serializers.Serializer):
    email = serializers.EmailField()
    password = serializers.CharField(write_only=True)

    def validate(self, data):
        email = data.get("email", "").strip().lower()
        password = data.get("password", "")
        if not email or not password:
            raise serializers.ValidationError("Email and password are required.")

        user = authenticate(username=email, password=password)
        if not user:
            found_user = User.objects.filter(email__iexact=email).first()
            if found_user and found_user.check_password(password):
                user = found_user

        if not user:
            raise serializers.ValidationError("Invalid email or password.")
        if not user.is_active or user.status in ("SUSPENDED", "DISABLED"):
            raise serializers.ValidationError("This account has been suspended or disabled.")
        data["user"] = user
        return data


class GoogleAuthSerializer(serializers.Serializer):
    credential = serializers.CharField(required=True)


class B2BUserCreateSerializer(serializers.Serializer):
    email = serializers.EmailField()
    first_name = serializers.CharField(max_length=100)
    last_name = serializers.CharField(max_length=100, required=False, allow_blank=True)
    phone = serializers.CharField(max_length=20, required=False, allow_blank=True)
    role = serializers.ChoiceField(choices=["STAFF", "ADMIN"], default="STAFF")
    status = serializers.ChoiceField(choices=["ACTIVE", "INVITED"], default="INVITED")
    department = serializers.CharField(max_length=100, required=False, default="Turf Operations")
    employee_id = serializers.CharField(max_length=50, required=False, allow_blank=True)

    def validate_email(self, value):
        if User.objects.filter(email__iexact=value).exists():
            raise serializers.ValidationError("A user with this email already exists.")
        return value.lower()


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

