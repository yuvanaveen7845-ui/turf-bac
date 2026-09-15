from django.contrib.contenttypes.management import create_contenttypes
from django.contrib.auth.management import create_permissions
from django.db.models.signals import post_migrate
import os
from pathlib import Path
from datetime import timedelta

BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = "friends-turf-secure-jwt-key-mongodb-development-2026-production-ready"
DEBUG = True
ALLOWED_HOSTS = ["*"]

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    # Third party apps
    "corsheaders",
    "rest_framework",
    "rest_framework_simplejwt",
    # Friends Turf apps
    "accounts.apps.AccountsConfig",
    "turfs.apps.TurfsConfig",
    "bookings.apps.BookingsConfig",
    "payments.apps.PaymentsConfig",
    "pricing.apps.PricingConfig",
    "promotions.apps.PromotionsConfig",
    "memberships.apps.MembershipsConfig",
    "wallet.apps.WalletConfig",
    "qr_system.apps.QrSystemConfig",
    "reviews.apps.ReviewsConfig",
    "notifications.apps.NotificationsConfig",
    "maintenance.apps.MaintenanceConfig",
    "reports.apps.ReportsConfig",
    "audit.apps.AuditConfig",
]

MIDDLEWARE = [
    "corsheaders.middleware.CorsMiddleware",
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "friends_turf.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "friends_turf.wsgi.application"

# Official MongoDB Database Backend
DATABASES = {
    "default": {
        "ENGINE": "django_mongodb_backend",
        "NAME": "friends_turf_db",
        "HOST": "mongodb://localhost:27017/friends_turf_db",
    }
}

AUTH_USER_MODEL = "accounts.User"

AUTH_PASSWORD_VALIDATORS = [
    {
        "NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
        "OPTIONS": {"min_length": 6},
    },
]

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": (
        "rest_framework_simplejwt.authentication.JWTAuthentication",
    ),
    "DEFAULT_PERMISSION_CLASSES": [
        "rest_framework.permissions.IsAuthenticatedOrReadOnly",
    ],
    "DEFAULT_PAGINATION_CLASS": "rest_framework.pagination.PageNumberPagination",
    "PAGE_SIZE": 20,
}

SIMPLE_JWT = {
    "ACCESS_TOKEN_LIFETIME": timedelta(days=7),
    "REFRESH_TOKEN_LIFETIME": timedelta(days=30),
    "ROTATE_REFRESH_TOKENS": True,
    "BLACKLIST_AFTER_ROTATION": False,
    "AUTH_HEADER_TYPES": ("Bearer",),
}

CORS_ALLOW_ALL_ORIGINS = True
CORS_ALLOW_CREDENTIALS = True

LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"

MEDIA_URL = "/media/"
MEDIA_ROOT = BASE_DIR / "media"

DEFAULT_AUTO_FIELD = "django_mongodb_backend.fields.ObjectIdAutoField"

SILENCED_SYSTEM_CHECKS = [
    "mongodb.fields.auto.E001",
]

# Map ObjectIdAutoField to CharField for Django REST Framework
try:
    from rest_framework import serializers
    from django_mongodb_backend.fields import ObjectIdAutoField

    serializers.ModelSerializer.serializer_field_mapping[ObjectIdAutoField] = (
        serializers.CharField
    )

    from bson import ObjectId
    from rest_framework.utils.encoders import JSONEncoder

    _orig_json_default = JSONEncoder.default

    def _custom_json_default(self, obj):
        if isinstance(obj, ObjectId):
            return str(obj)
        return _orig_json_default(self, obj)

    JSONEncoder.default = _custom_json_default
except Exception:
    pass

# Disconnect post_migrate signals for permissions and content types in MongoDB

post_migrate.disconnect(
    create_permissions, dispatch_uid="django.contrib.auth.management.create_permissions"
)
post_migrate.disconnect(
    create_contenttypes,
    dispatch_uid="django.contrib.contenttypes.management.create_contenttypes",
)
