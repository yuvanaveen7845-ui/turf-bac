from rest_framework import serializers
from django.contrib.auth import authenticate
from .models import User, CustomerProfile, StaffProfile


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

