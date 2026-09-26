from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static

from django.http import JsonResponse

def api_root(request):
    return JsonResponse({
        "status": "healthy",
        "service": "Friends Turf API",
        "endpoints": {
            "admin": "/admin/",
            "auth": "/api/auth/",
            "turfs": "/api/turfs/",
            "bookings": "/api/bookings/",
            "payments": "/api/payments/",
            "realtime": "/api/realtime/stream/",
        }
    })


def health_check(request):
    """
    Lightweight health endpoint for keep-alive pings (e.g. UptimeRobot).
    Does a minimal DB query to keep the connection pool warm and prevent
    Render cold starts from killing perceived performance.
    """
    from django.db import connection
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
        db_ok = True
    except Exception:
        db_ok = False
    status_code = 200 if db_ok else 503
    return JsonResponse({"status": "ok" if db_ok else "degraded", "db": db_ok}, status=status_code)

# Shared API route patterns available under both /api/... and root /...
api_patterns = [
    path("auth/", include("accounts.urls")),
    path("accounts/", include("accounts.urls")),
    path("turfs/", include("turfs.urls")),
    path("bookings/", include("bookings.urls")),
    path("payments/", include("payments.urls")),
    path("pricing/", include("pricing.urls")),
    path("promotions/", include("promotions.urls")),
    path("memberships/", include("memberships.urls")),
    path("wallet/", include("wallet.urls")),
    path("qr/", include("qr_system.urls")),
    path("reviews/", include("reviews.urls")),
    path("notifications/", include("notifications.urls")),
    path("maintenance/", include("maintenance.urls")),
    path("reports/", include("reports.urls")),
    path("audit/", include("audit.urls")),
    path("realtime/", include("realtime.urls")),
]

urlpatterns = [
    path("", api_root, name="api_root"),
    path("admin/", admin.site.urls),
    path("api/health/", health_check, name="health_check"),
    path("api/health", health_check),
    path("health/", health_check),
    path("health", health_check),
    
    # API endpoints under both /api/... and direct /...
    path("api/", include(api_patterns)),
    path("", include(api_patterns)),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
