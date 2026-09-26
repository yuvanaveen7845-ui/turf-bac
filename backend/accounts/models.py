import uuid
import hashlib
import secrets
import re
from django.db import models
from django.contrib.auth.models import (
    AbstractBaseUser,
    PermissionsMixin,
    BaseUserManager,
)
from django.utils import timezone
from django.core.exceptions import PermissionDenied
from django.db.models.signals import pre_delete
from django.dispatch import receiver

PROTECTED_SUPERADMIN_EMAILS = {"friendsturf171@gmail.com"}


class UserQuerySet(models.QuerySet):
    def delete(self):
        for protected in PROTECTED_SUPERADMIN_EMAILS:
            if self.filter(email__iexact=protected).exists():
                raise PermissionDenied(
                    f"Permanent system administrator '{protected}' is protected and cannot be deleted."
                )
        return super().delete()


class UserManager(BaseUserManager):
    def get_queryset(self):
        return UserQuerySet(self.model, using=self._db)

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

    PROTECTED_SUPERADMIN_EMAILS = PROTECTED_SUPERADMIN_EMAILS

    def is_permanent_admin(self) -> bool:
        """Returns True if user is a designated undeletable root system administrator."""
        return bool(self.email and self.email.strip().lower() in PROTECTED_SUPERADMIN_EMAILS)

    def save(self, *args, **kwargs):
        # Prevent mutating the email address of the permanent superadmin
        if self.pk:
            orig = User.objects.filter(pk=self.pk).values("email").first()
            if (
                orig
                and orig["email"].strip().lower() in PROTECTED_SUPERADMIN_EMAILS
                and self.email.strip().lower() != orig["email"].strip().lower()
            ):
                raise PermissionDenied(
                    f"Cannot change email address of permanent system administrator '{orig['email']}'."
                )
        if not self.referral_code:
            code = f"FT{uuid.uuid4().hex[:6].upper()}"
            while User.objects.filter(referral_code=code).exists():
                code = f"FT{uuid.uuid4().hex[:6].upper()}"
            self.referral_code = code
        super().save(*args, **kwargs)
        if self.role == "CUSTOMER":
            CustomerProfile.objects.get_or_create(user=self)
        elif self.role in ("STAFF", "ADMIN"):
            StaffProfile.objects.get_or_create(user=self)

    def delete(self, *args, **kwargs):
        if self.is_permanent_admin():
            raise PermissionDenied(
                f"Permanent system administrator '{self.email}' is protected and cannot be deleted."
            )
        return super().delete(*args, **kwargs)

    def __str__(self):
        return f"{self.email} ({self.role}) [{self.status}]"

    @property
    def full_name(self):
        return f"{self.first_name} {self.last_name}".strip() or self.email

    def get_full_name(self):
        return self.full_name

    def get_short_name(self):
        return self.first_name or self.email

    @staticmethod
    def canonicalize_email(email: str) -> str:
        """Standardize email address to lowercase and trimmed string."""
        return (email or "").strip().lower()

    @staticmethod
    def canonicalize_phone(phone: str) -> str:
        """Standardize Indian phone number to 10-digit clean string or E.164 (+91...)."""
        clean = re.sub(r"[^\d+]", "", str(phone or "").strip())
        if clean.startswith("+91") and len(clean) == 13:
            return clean
        if clean.startswith("91") and len(clean) == 12:
            return f"+{clean}"
        if len(clean) == 10 and clean[0] in "6789":
            return f"+91{clean}"
        return clean


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


class PasswordResetOTP(models.Model):
    """
    Cryptographic OTP model for Password Reset & Verification.
    Stores SHA-256 hashed 6-digit OTP code, attempt counter, and single-use session tokens.
    """
    user = models.ForeignKey(
        User, on_delete=models.CASCADE, related_name="password_reset_otps"
    )
    otp_hash = models.CharField(max_length=64, db_index=True)
    reset_token = models.CharField(
        max_length=64, blank=True, unique=True, null=True, db_index=True
    )
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField(db_index=True)
    attempts = models.IntegerField(default=0)
    max_attempts = models.IntegerField(default=5)
    is_verified = models.BooleanField(default=False)
    is_used = models.BooleanField(default=False)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.TextField(blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["user", "is_verified", "is_used", "expires_at"]),
        ]

    def __str__(self):
        return f"OTP for {self.user.email} [{self.is_verified=}, {self.is_used=}]"

    @staticmethod
    def hash_otp(otp_code: str) -> str:
        """Computes SHA-256 cryptographic hash of numeric OTP."""
        return hashlib.sha256(str(otp_code).strip().encode("utf-8")).hexdigest()

    def is_expired(self) -> bool:
        return timezone.now() > self.expires_at

    def is_locked(self) -> bool:
        return self.attempts >= self.max_attempts

    def verify_code(self, candidate_otp: str) -> bool:
        """Verifies candidate 6-digit OTP against stored SHA-256 hash."""
        if self.is_used or self.is_verified or self.is_expired() or self.is_locked():
            return False
        self.attempts += 1
        candidate_hash = self.hash_otp(candidate_otp)
        if candidate_hash == self.otp_hash:
            self.is_verified = True
            self.reset_token = uuid.uuid4().hex + uuid.uuid4().hex[:16]
            self.save(update_fields=["attempts", "is_verified", "reset_token"])
            return True
        self.save(update_fields=["attempts"])
        return False

    @classmethod
    def generate_otp_for_user(
        cls, user, validity_minutes=10, ip_address=None, user_agent=""
    ):
        """
        Invalidates existing unverified OTPs and generates a fresh 6-digit numeric OTP.
        Returns a tuple: (otp_instance, raw_6_digit_otp_string).
        """
        # Invalidate existing active OTPs
        cls.objects.filter(user=user, is_used=False).update(is_used=True)

        # Secure 6-digit numeric code
        raw_code = f"{secrets.randbelow(900000) + 100000}"
        otp_hash = cls.hash_otp(raw_code)
        expires_at = timezone.now() + timezone.timedelta(minutes=validity_minutes)

        clean_ip = None
        if ip_address:
            first_ip = str(ip_address).split(",")[0].strip()
            if first_ip and len(first_ip) <= 45:
                clean_ip = first_ip

        instance = cls.objects.create(
            user=user,
            otp_hash=otp_hash,
            expires_at=expires_at,
            ip_address=clean_ip,
            user_agent=str(user_agent or "")[:500],
        )
        return instance, raw_code



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


@receiver(pre_delete, sender=User)
def protect_permanent_admin_pre_delete(sender, instance, **kwargs):
    if instance.is_permanent_admin():
        raise PermissionDenied(
            f"Permanent system administrator '{instance.email}' is protected and cannot be deleted."
        )


@receiver(pre_delete, sender=StaffProfile)
def protect_permanent_admin_staff_profile_pre_delete(sender, instance, **kwargs):
    if instance.user and instance.user.is_permanent_admin():
        raise PermissionDenied(
            "Cannot delete staff profile belonging to permanent system administrator."
        )

