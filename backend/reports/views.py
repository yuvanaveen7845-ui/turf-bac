from datetime import datetime, date, timedelta
from decimal import Decimal
from rest_framework import status, views, permissions
from rest_framework.response import Response
from django.utils import timezone
from django.db.models import Sum, Count, Avg

from bookings.models import Booking
from turfs.models import Turf, TimeSlot
from accounts.models import User
from payments.models import Payment
from reviews.models import Review
from accounts.permissions import IsStaffOrAdmin


class AdminDashboardMetricsView(views.APIView):
    permission_classes = [IsStaffOrAdmin]

    def get(self, request):
        today = timezone.now().date()
        today_start = timezone.now().replace(hour=0, minute=0, second=0, microsecond=0)

        # 1. Today's Bookings & Revenue
        today_bookings = Booking.objects.filter(date=today)
        today_count = today_bookings.count()
        today_revenue = today_bookings.filter(
            status__in=["CONFIRMED", "CHECKED_IN", "IN_PROGRESS", "COMPLETED"]
        ).aggregate(Sum("amount_paid"))["amount_paid__sum"] or Decimal("0.00")

        # 2. Upcoming, Pending, Cancellations, No-shows
        upcoming_count = Booking.objects.filter(
            date__gte=today, status__in=["CONFIRMED", "UPCOMING"]
        ).count()
        pending_payments = Booking.objects.filter(status="PAYMENT_PENDING").count()
        cancellations_count = Booking.objects.filter(status="CANCELLED").count()
        noshow_count = Booking.objects.filter(status="NO_SHOW").count()

        # 3. Available vs Total slots today
        total_slots_today = TimeSlot.objects.filter(date=today).count()
        booked_slots_today = TimeSlot.objects.filter(
            date=today, status="BOOKED"
        ).count()
        available_slots_today = TimeSlot.objects.filter(
            date=today, status="AVAILABLE"
        ).count()
        occupancy_rate = (
            round((booked_slots_today / total_slots_today * 100), 1)
            if total_slots_today > 0
            else 0.0
        )

        # 4. Customers & Ratings
        active_customers = User.objects.filter(role="CUSTOMER").count()
        avg_rating = (
            Review.objects.filter(is_hidden=False).aggregate(Avg("rating"))[
                "rating__avg"
            ]
            or 4.9
        )

        # 5. Last 7 Days Revenue Trend
        revenue_trend = []
        for i in range(6, -1, -1):
            day = today - timedelta(days=i)
            day_rev = Booking.objects.filter(
                date=day,
                status__in=["CONFIRMED", "CHECKED_IN", "IN_PROGRESS", "COMPLETED"],
            ).aggregate(Sum("amount_paid"))["amount_paid__sum"] or Decimal("0.00")
            day_bookings_cnt = Booking.objects.filter(date=day).count()
            revenue_trend.append(
                {
                    "date": day.strftime("%a, %d %b"),
                    "revenue": float(day_rev),
                    "bookings": day_bookings_cnt,
                }
            )

        # 6. Peak Hours Analysis (distribution of bookings by start_hour)
        all_bookings = Booking.objects.all()
        hour_counts = {}
        for b in all_bookings:
            hr_str = b.start_time.strftime("%I %p")
            hour_counts[hr_str] = hour_counts.get(hr_str, 0) + 1

        peak_hours = [
            {"hour": hr, "count": cnt}
            for hr, cnt in sorted(
                hour_counts.items(), key=lambda x: x[1], reverse=True
            )[:8]
        ]

        # 7. Turf Utilization
        turfs = Turf.objects.all()
        turf_utilization = []
        for t in turfs:
            t_booked = Booking.objects.filter(turf=t).count()
            turf_utilization.append(
                {
                    "turf_name": t.name,
                    "sport": t.sport_type,
                    "total_bookings": t_booked,
                    "base_price": float(t.base_price),
                }
            )

        return Response(
            {
                "kpis": {
                    "today_bookings": today_count,
                    "today_revenue": float(today_revenue),
                    "upcoming_bookings": upcoming_count,
                    "pending_payments": pending_payments,
                    "cancellations": cancellations_count,
                    "no_shows": noshow_count,
                    "occupancy_rate": occupancy_rate,
                    "available_slots": available_slots_today,
                    "active_customers": active_customers,
                    "average_rating": round(avg_rating, 2),
                },
                "revenue_trend": revenue_trend,
                "peak_hours": peak_hours,
                "turf_utilization": turf_utilization,
            }
        )


class BusinessReportsView(views.APIView):
    permission_classes = [IsStaffOrAdmin]

    def get(self, request):
        today = timezone.now().date()
        bookings = Booking.objects.filter(date=today)

        total_collected = bookings.aggregate(Sum("amount_paid"))[
            "amount_paid__sum"
        ] or Decimal("0.00")
        pending_total = bookings.aggregate(Sum("balance_due"))[
            "balance_due__sum"
        ] or Decimal("0.00")

        summary = {
            "report_date": today.strftime("%d %B %Y"),
            "total_bookings": bookings.count(),
            "confirmed_bookings": bookings.filter(status="CONFIRMED").count(),
            "checked_in_bookings": bookings.filter(status="CHECKED_IN").count(),
            "completed_bookings": bookings.filter(status="COMPLETED").count(),
            "cancelled_bookings": bookings.filter(status="CANCELLED").count(),
            "revenue_collected": float(total_collected),
            "pending_receivables": float(pending_total),
            "generated_at": timezone.now().strftime("%Y-%m-%d %H:%M:%S"),
        }
        return Response(summary)
