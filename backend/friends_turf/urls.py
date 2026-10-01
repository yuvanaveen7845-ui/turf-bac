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

import os
from django.views.static import serve
from django.urls import re_path
from django.http import HttpResponse

TURF_FALLBACK_SVG = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 800 500" width="100%" height="100%">
  <defs>
    <linearGradient id="grass" x1="0%" y1="0%" x2="100%" y2="100%">
      <stop offset="0%" stop-color="#064e3b" />
      <stop offset="50%" stop-color="#047857" />
      <stop offset="100%" stop-color="#065f46" />
    </linearGradient>
    <radialGradient id="floodlight" cx="50%" cy="40%" r="60%">
      <stop offset="0%" stop-color="#ffffff" stop-opacity="0.25" />
      <stop offset="100%" stop-color="#000000" stop-opacity="0.4" />
    </radialGradient>
    <pattern id="stripes" width="80" height="500" patternUnits="userSpaceOnUse">
      <rect width="40" height="500" fill="#ffffff" fill-opacity="0.04" />
    </pattern>
  </defs>
  <rect width="800" height="500" fill="url(#grass)" />
  <rect width="800" height="500" fill="url(#stripes)" />
  <rect width="800" height="500" fill="url(#floodlight)" />
  <rect x="40" y="30" width="720" height="440" rx="4" fill="none" stroke="#ffffff" stroke-width="3" stroke-opacity="0.75" />
  <line x1="400" y1="30" x2="400" y2="470" stroke="#ffffff" stroke-width="3" stroke-opacity="0.75" />
  <circle cx="400" cy="250" r="65" fill="none" stroke="#ffffff" stroke-width="3" stroke-opacity="0.75" />
  <circle cx="400" cy="250" r="4" fill="#ffffff" fill-opacity="0.9" />
  <rect x="40" y="140" width="120" height="220" fill="none" stroke="#ffffff" stroke-width="3" stroke-opacity="0.75" />
  <rect x="40" y="190" width="45" height="120" fill="none" stroke="#ffffff" stroke-width="3" stroke-opacity="0.75" />
  <circle cx="120" cy="250" r="3" fill="#ffffff" fill-opacity="0.9" />
  <rect x="640" y="140" width="120" height="220" fill="none" stroke="#ffffff" stroke-width="3" stroke-opacity="0.75" />
  <rect x="715" y="190" width="45" height="120" fill="none" stroke="#ffffff" stroke-width="3" stroke-opacity="0.75" />
  <circle cx="680" cy="250" r="3" fill="#ffffff" fill-opacity="0.9" />
</svg>"""

def safe_media_serve(request, path):
    """
    Safely serve uploaded media. If Render ephemeral storage wiped the file on container restart,
    seamlessly return the vector turf SVG with 200 OK and CORS headers instead of throwing 404.
    """
    full_path = os.path.join(settings.MEDIA_ROOT, path)
    if os.path.exists(full_path) and os.path.isfile(full_path):
        resp = serve(request, path, document_root=settings.MEDIA_ROOT)
        resp["Access-Control-Allow-Origin"] = "*"
        return resp

    resp = HttpResponse(TURF_FALLBACK_SVG, content_type="image/svg+xml")
    resp["Access-Control-Allow-Origin"] = "*"
    resp["Cache-Control"] = "public, max-age=86400"
    return resp

urlpatterns += [
    re_path(r"^media/(?P<path>.*)$", safe_media_serve),
]
