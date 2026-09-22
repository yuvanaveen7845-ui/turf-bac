import os
from pathlib import Path
from datetime import timedelta
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent

# Load .env file
load_dotenv(BASE_DIR / ".env", override=True)

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

# In-process cache for BusinessSettings, pricing contexts, and other hot-path data.
# LocMemCache requires no external infrastructure (Redis, Memcached) — ideal for
# single-dyno Render deployments. Each gunicorn worker gets its own cache instance.
CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "friends-turf-cache",
        "TIMEOUT": 300,  # 5 minutes default TTL
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

DEFAULT_CORS_ALLOWED_ORIGINS = [
    "https://friendsturf.in",
    "https://www.friendsturf.in",
    "https://friendsturf.com",
    "https://www.friendsturf.com",
    "https://turf-fron.pages.dev",
    "http://localhost:5173",
    "http://localhost:5174",
    "http://127.0.0.1:5173",
    "http://127.0.0.1:5174",
]

cors_origins_env = os.getenv("CORS_ALLOWED_ORIGINS", "").strip()
if cors_origins_env:
    env_origins = [origin.strip() for origin in cors_origins_env.split(",") if origin.strip()]
    CORS_ALLOWED_ORIGINS = list(dict.fromkeys(DEFAULT_CORS_ALLOWED_ORIGINS + env_origins))
else:
    CORS_ALLOWED_ORIGINS = DEFAULT_CORS_ALLOWED_ORIGINS

CORS_ALLOW_ALL_ORIGINS = False
CORS_ALLOW_CREDENTIALS = True
CORS_ALLOWED_ORIGIN_REGEXES = [
    r"^https:\/\/.*\.onrender\.com$",
    r"^https:\/\/.*\.vercel\.app$",
    r"^https:\/\/.*\.pages\.dev$",
    r"^https:\/\/.*\.friendsturf\.in$",
    r"^https:\/\/.*\.friendsturf\.com$",
]

# CSRF Trusted Origins for HTTPS requests in production
DEFAULT_CSRF_TRUSTED_ORIGINS = [
    "https://*.onrender.com",
    "https://*.vercel.app",
    "https://*.pages.dev",
    "https://turf-fron.pages.dev",
    "https://friendsturf.in",
    "https://*.friendsturf.in",
    "https://friendsturf.com",
    "https://*.friendsturf.com",
    "http://localhost:5173",
    "http://localhost:5174",
    "http://127.0.0.1:5173",
    "http://127.0.0.1:5174",
]

csrf_origins_env = os.getenv("CSRF_TRUSTED_ORIGINS", "").strip()
if csrf_origins_env:
    env_csrf = [origin.strip() for origin in csrf_origins_env.split(",") if origin.strip()]
    CSRF_TRUSTED_ORIGINS = list(dict.fromkeys(DEFAULT_CSRF_TRUSTED_ORIGINS + env_csrf))
else:
    CSRF_TRUSTED_ORIGINS = DEFAULT_CSRF_TRUSTED_ORIGINS

# Production Security Configurations
SECURE_CROSS_ORIGIN_OPENER_POLICY = os.getenv("SECURE_CROSS_ORIGIN_OPENER_POLICY", "same-origin-allow-popups")

if not DEBUG:
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_SSL_REDIRECT = os.getenv("SECURE_SSL_REDIRECT", "True").lower() in ("true", "1", "yes")
    SECURE_HSTS_SECONDS = int(os.getenv("SECURE_HSTS_SECONDS", "31536000"))
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True
    SECURE_HSTS_PRELOAD = True

LANGUAGE_CODE = "en-us"
TIME_ZONE = os.getenv("TIME_ZONE", "Asia/Kolkata")
USE_I18N = True
USE_TZ = True

STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"

MEDIA_URL = "/media/"
MEDIA_ROOT = BASE_DIR / "media"

# Frontend URL for email passes, redirects and notifications
FRONTEND_URL = os.getenv("FRONTEND_URL", "http://localhost:5173").rstrip("/")

# Google OAuth Settings (B2B Authentication)
GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID", "")
GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET", "")
GOOGLE_REDIRECT_URI = os.getenv("GOOGLE_REDIRECT_URI", "http://localhost:5174/login")
GOOGLE_ALLOWED_DOMAIN = os.getenv("GOOGLE_ALLOWED_DOMAIN", "")

# Razorpay Payment Gateway Settings (Fetched strictly from Environment / .env)
RAZORPAY_KEY_ID = os.getenv("RAZORPAY_KEY_ID", "").strip()
RAZORPAY_KEY_SECRET = os.getenv("RAZORPAY_KEY_SECRET", "").strip()
RAZORPAY_WEBHOOK_SECRET = os.getenv("RAZORPAY_WEBHOOK_SECRET", "").strip()

# Django SMTP Email Configuration
EMAIL_HOST = os.getenv("EMAIL_HOST", "smtp.gmail.com").strip()
EMAIL_PORT = int(os.getenv("EMAIL_PORT", "587").strip() or 587)
EMAIL_USE_TLS = os.getenv("EMAIL_USE_TLS", "True").lower() in ("true", "1", "yes")
EMAIL_USE_SSL = os.getenv("EMAIL_USE_SSL", "False").lower() in ("true", "1", "yes")
EMAIL_HOST_USER = os.getenv("EMAIL_HOST_USER", "").strip()
EMAIL_HOST_PASSWORD = os.getenv("EMAIL_HOST_PASSWORD", "").strip()

# Authoritative default From email matching authenticated identity
default_from = os.getenv("DEFAULT_FROM_EMAIL", "").strip()
if not default_from or ("@friendsturf.com" in default_from and EMAIL_HOST_USER and "gmail.com" in EMAIL_HOST.lower() and not EMAIL_HOST_USER.endswith("@friendsturf.com")):
    default_from = f"Friends Turf <{EMAIL_HOST_USER}>" if EMAIL_HOST_USER else "Friends Turf <noreply@friendsturf.com>"

DEFAULT_FROM_EMAIL = default_from
SERVER_EMAIL = DEFAULT_FROM_EMAIL

# Default to SMTP backend if host user is configured, otherwise fallback to console backend in dev
default_email_backend = (
    "django.core.mail.backends.smtp.EmailBackend"
    if EMAIL_HOST_USER
    else "django.core.mail.backends.console.EmailBackend"
)
EMAIL_BACKEND = os.getenv("EMAIL_BACKEND", default_email_backend).strip()



LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "verbose": {
            "format": "[%(asctime)s] %(levelname)s %(name)s (line %(lineno)d): %(message)s",
        },
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "verbose",
        },
    },
    "root": {
        "handlers": ["console"],
        "level": "INFO",
    },
    "loggers": {
        "django": {
            "handlers": ["console"],
            "level": "INFO",
            "propagate": False,
        },
        "django.request": {
            "handlers": ["console"],
            "level": "ERROR",
            "propagate": False,
        },
    },
}

