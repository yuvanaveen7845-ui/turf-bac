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
    
    # API endpoints under both /api/... and direct /...
    path("api/", include(api_patterns)),
    path("", include(api_patterns)),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
