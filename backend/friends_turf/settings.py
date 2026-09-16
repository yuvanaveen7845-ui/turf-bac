import os
from pathlib import Path
from datetime import timedelta
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent

# Load .env file
load_dotenv(BASE_DIR / ".env")

SECRET_KEY = os.getenv("SECRET_KEY", "friends-turf-secure-jwt-key-2026-production")
DEBUG = os.getenv("DEBUG", "True").lower() in ("true", "1", "yes")
allowed_hosts_env = os.getenv("ALLOWED_HOSTS", "").strip()
if allowed_hosts_env:
    ALLOWED_HOSTS = [h.strip() for h in allowed_hosts_env.split(",") if h.strip()]
else:
    ALLOWED_HOSTS = ["*", ".onrender.com", "localhost", "127.0.0.1"]

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
    "realtime.apps.RealtimeConfig",
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

# Database Configuration
# - Production: Supabase PostgreSQL (via DATABASE_URL or individual DB_* settings)
# - Local Development: SQLite (default fallback) or local PostgreSQL
DATABASE_URL = os.environ.get("DATABASE_URL", "").strip()

if DATABASE_URL:
    import dj_database_url

    is_pooler = "pooler.supabase.com" in DATABASE_URL or ":6543" in DATABASE_URL
    db_config = dj_database_url.config(
        default=DATABASE_URL,
        conn_max_age=0 if is_pooler else 600,
        conn_health_checks=True,
        ssl_require=os.environ.get("DB_SSL_REQUIRE", "true").lower() in ("true", "1", "yes"),
    )
    if is_pooler:
        db_config["DISABLE_SERVER_SIDE_CURSORS"] = True
    DATABASES = {
        "default": db_config
    }
elif os.environ.get("DB_ENGINE") in ("postgres", "postgresql") or os.environ.get("DB_HOST"):
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": os.environ.get("DB_NAME", "postgres"),
            "USER": os.environ.get("DB_USER", "postgres"),
            "PASSWORD": os.environ.get("DB_PASSWORD", ""),
            "HOST": os.environ.get("DB_HOST", "localhost"),
            "PORT": os.environ.get("DB_PORT", "5432"),
            "CONN_MAX_AGE": 600,
            "CONN_HEALTH_CHECKS": True,
        }
    }
else:
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": BASE_DIR / "db.sqlite3",
        }
    }

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
SILENCED_SYSTEM_CHECKS = []

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
}


SIMPLE_JWT = {
    "ACCESS_TOKEN_LIFETIME": timedelta(days=7),
    "REFRESH_TOKEN_LIFETIME": timedelta(days=30),
    "ROTATE_REFRESH_TOKENS": True,
    "BLACKLIST_AFTER_ROTATION": False,
    "AUTH_HEADER_TYPES": ("Bearer",),
}

# Proxy SSL Header for Render, Heroku, Cloudflare, AWS load balancers
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")

cors_origins_env = os.getenv("CORS_ALLOWED_ORIGINS", "").strip()
if cors_origins_env:
    CORS_ALLOWED_ORIGINS = [origin.strip() for origin in cors_origins_env.split(",") if origin.strip()]
    CORS_ALLOW_ALL_ORIGINS = False
else:
    CORS_ALLOW_ALL_ORIGINS = True
CORS_ALLOW_CREDENTIALS = True
CORS_ALLOWED_ORIGIN_REGEXES = [
    r"^https:\/\/.*\.onrender\.com$",
    r"^https:\/\/.*\.vercel\.app$",
    r"^https:\/\/.*\.pages\.dev$",
]

# CSRF Trusted Origins for HTTPS requests in production
csrf_origins_env = os.getenv("CSRF_TRUSTED_ORIGINS", "").strip()
if csrf_origins_env:
    CSRF_TRUSTED_ORIGINS = [origin.strip() for origin in csrf_origins_env.split(",") if origin.strip()]
else:
    CSRF_TRUSTED_ORIGINS = [
        "https://*.onrender.com",
        "https://*.vercel.app",
        "https://*.pages.dev",
        "https://turf-fron.pages.dev",
        "http://localhost:5173",
        "http://localhost:5174",
        "http://127.0.0.1:5173",
        "http://127.0.0.1:5174",
    ]

# Production Security Configurations
if not DEBUG:
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_SSL_REDIRECT = os.getenv("SECURE_SSL_REDIRECT", "True").lower() in ("true", "1", "yes")
    SECURE_HSTS_SECONDS = int(os.getenv("SECURE_HSTS_SECONDS", "31536000"))
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True
    SECURE_HSTS_PRELOAD = True

LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"

MEDIA_URL = "/media/"
MEDIA_ROOT = BASE_DIR / "media"

# Google OAuth Settings (B2B Authentication)
GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID", "")
GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET", "")
GOOGLE_REDIRECT_URI = os.getenv("GOOGLE_REDIRECT_URI", "http://localhost:5174/login")
GOOGLE_ALLOWED_DOMAIN = os.getenv("GOOGLE_ALLOWED_DOMAIN", "")

# Razorpay Payment Gateway Settings
RAZORPAY_KEY_ID = os.getenv("RAZORPAY_KEY_ID", "rzp_test_FriendsTurfKey")
RAZORPAY_KEY_SECRET = os.getenv("RAZORPAY_KEY_SECRET", "razorpay_test_secret_key")
RAZORPAY_WEBHOOK_SECRET = os.getenv("RAZORPAY_WEBHOOK_SECRET", "razorpay_test_webhook_secret")

