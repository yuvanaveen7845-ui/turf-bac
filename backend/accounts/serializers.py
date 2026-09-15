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


class UserSerializer(serializers.ModelSerializer):
    customer_profile = CustomerProfileSerializer(read_only=True)
    staff_profile = StaffProfileSerializer(read_only=True)
    full_name = serializers.ReadOnlyField()

    class Meta:
        model = User
        fields = [
            "id",
            "email",
            "first_name",
            "last_name",
            "full_name",
            "phone",
            "role",
            "referral_code",
            "date_joined",
            "customer_profile",
            "staff_profile",
        ]
        read_only_fields = ["id", "referral_code", "date_joined"]


class RegisterSerializer(serializers.Serializer):
    email = serializers.EmailField()
    password = serializers.CharField(write_only=True, min_length=6)
    first_name = serializers.CharField(max_length=100)
    last_name = serializers.CharField(max_length=100, required=False, allow_blank=True)
    phone = serializers.CharField(max_length=20, required=False, allow_blank=True)
    referral_code = serializers.CharField(
        max_length=20, required=False, allow_blank=True
    )
    role = serializers.ChoiceField(choices=User.ROLE_CHOICES, default="CUSTOMER")

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

        user = User.objects.create_user(
            email=validated_data["email"],
            password=validated_data["password"],
            first_name=validated_data.get("first_name", ""),
            last_name=validated_data.get("last_name", ""),
            phone=validated_data.get("phone", ""),
            role=validated_data.get("role", "CUSTOMER"),
            referred_by=referred_by,
        )
        return user


class LoginSerializer(serializers.Serializer):
    email = serializers.EmailField()
    password = serializers.CharField(write_only=True)

    def validate(self, data):
        email = data.get("email", "").lower()
        password = data.get("password")
        user = authenticate(username=email, password=password)
        if not user:
            raise serializers.ValidationError("Invalid email or password.")
        if not user.is_active:
            raise serializers.ValidationError("This account has been disabled.")
        data["user"] = user
        return data
