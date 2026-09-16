import uuid
from django.db import models
from django.contrib.auth.models import (
    AbstractBaseUser,
    PermissionsMixin,
    BaseUserManager,
)
from django.utils import timezone


class UserManager(BaseUserManager):
    def create_user(self, email, password=None, **extra_fields):
        if not email:
            raise ValueError("Email address is required")
        email = self.normalize_email(email)
        extra_fields.setdefault("role", "CUSTOMER")
        user = self.model(email=email, **extra_fields)
        if password:
            user.set_password(password)
        else:
            user.set_unusable_password()
        if not user.referral_code:
            user.referral_code = f"FT{uuid.uuid4().hex[:6].upper()}"
        user.save(using=self._db)

        if user.role == "CUSTOMER":
            CustomerProfile.objects.get_or_create(user=user)
        elif user.role in ("STAFF", "ADMIN"):
            StaffProfile.objects.get_or_create(user=user)

        return user

    def create_superuser(self, email, password=None, **extra_fields):
        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", True)
        extra_fields.setdefault("role", "ADMIN")
        extra_fields.setdefault("status", "ACTIVE")
        return self.create_user(email, password, **extra_fields)


class User(AbstractBaseUser, PermissionsMixin):
    ROLE_CHOICES = (
        ("CUSTOMER", "Customer"),
        ("STAFF", "Staff"),
        ("ADMIN", "Admin"),
    )

    STATUS_CHOICES = (
        ("ACTIVE", "Active"),
        ("INVITED", "Invited"),
        ("SUSPENDED", "Suspended"),
        ("DISABLED", "Disabled"),
    )

    email = models.EmailField(unique=True, db_index=True)
    google_id = models.CharField(
        max_length=100, blank=True, null=True, unique=True, db_index=True
    )
    first_name = models.CharField(max_length=100, blank=True)
    last_name = models.CharField(max_length=100, blank=True)
    profile_image = models.URLField(max_length=500, blank=True)
    phone = models.CharField(max_length=20, blank=True)
    role = models.CharField(max_length=20, choices=ROLE_CHOICES, default="CUSTOMER")
    status = models.CharField(
        max_length=20, choices=STATUS_CHOICES, default="ACTIVE", db_index=True
    )

    referral_code = models.CharField(max_length=20, unique=True, blank=True)
    referred_by = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="referrals",
    )

    is_active = models.BooleanField(default=True)
    is_staff = models.BooleanField(default=False)
    date_joined = models.DateTimeField(default=timezone.now)
    last_login_at = models.DateTimeField(null=True, blank=True)

    objects = UserManager()

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = ["first_name"]

    def __str__(self):
        return f"{self.email} ({self.role}) [{self.status}]"

    @property
    def full_name(self):
        return f"{self.first_name} {self.last_name}".strip() or self.email

    def get_full_name(self):
        return self.full_name


class CustomerProfile(models.Model):
    user = models.OneToOneField(
        User, on_delete=models.CASCADE, related_name="customer_profile"
    )
    wallet_balance = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)
    loyalty_points = models.IntegerField(default=0)
    membership_tier = models.CharField(max_length=30, default="REGULAR")
    total_bookings = models.IntegerField(default=0)
    total_spending = models.DecimalField(max_digits=12, decimal_places=2, default=0.00)
    cancellation_count = models.IntegerField(default=0)
    no_show_count = models.IntegerField(default=0)
    birthday = models.DateField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Customer: {self.user.email}"


class StaffProfile(models.Model):
    user = models.OneToOneField(
        User, on_delete=models.CASCADE, related_name="staff_profile"
    )
    employee_id = models.CharField(max_length=50, blank=True)
    department = models.CharField(max_length=100, default="Turf Operations")
    is_on_duty = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Staff: {self.user.email} [{self.employee_id or 'No ID'}]"


class PasswordResetToken(models.Model):
    user = models.ForeignKey(
        User, on_delete=models.CASCADE, related_name="password_reset_tokens"
    )
    token = models.CharField(max_length=100, unique=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    is_used = models.BooleanField(default=False)

    class Meta:
        ordering = ["-created_at"]

    def is_valid(self):
        return not self.is_used and timezone.now() <= self.expires_at

    @classmethod
    def generate_token_for_user(cls, user, validity_hours=1):
        # Invalidate existing active tokens
        cls.objects.filter(user=user, is_used=False).update(is_used=True)
        token_str = uuid.uuid4().hex + uuid.uuid4().hex[:16]
        expires_at = timezone.now() + timezone.timedelta(hours=validity_hours)
        return cls.objects.create(
            user=user,
            token=token_str,
            expires_at=expires_at,
        )


class CustomerNote(models.Model):
    customer = models.ForeignKey(
        User, on_delete=models.CASCADE, related_name="crm_notes"
    )
    author = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, related_name="authored_crm_notes"
    )
    note = models.TextField()
    is_pinned = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-is_pinned", "-created_at"]

    def __str__(self):
        return f"Note on {self.customer.email} by {self.author.email if self.author else 'System'}"


class BusinessSetting(models.Model):
    key = models.CharField(max_length=100, unique=True, db_index=True)
    value = models.JSONField(default=dict)
    updated_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True
    )
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.key

