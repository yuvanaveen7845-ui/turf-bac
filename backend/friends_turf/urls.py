from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static

urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/auth/", include("accounts.urls")),
    path("api/turfs/", include("turfs.urls")),
    path("api/bookings/", include("bookings.urls")),
    path("api/payments/", include("payments.urls")),
    path("api/pricing/", include("pricing.urls")),
    path("api/promotions/", include("promotions.urls")),
    path("api/memberships/", include("memberships.urls")),
    path("api/wallet/", include("wallet.urls")),
    path("api/qr/", include("qr_system.urls")),
    path("api/reviews/", include("reviews.urls")),
    path("api/notifications/", include("notifications.urls")),
    path("api/maintenance/", include("maintenance.urls")),
    path("api/reports/", include("reports.urls")),
    path("api/audit/", include("audit.urls")),
    path("api/realtime/", include("realtime.urls")),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
