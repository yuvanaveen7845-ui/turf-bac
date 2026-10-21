from rest_framework import status, views, generics, permissions
from rest_framework.response import Response
from django.shortcuts import get_object_or_404
from django.db.models import Avg

from .models import Review
from .serializers import ReviewSerializer
from bookings.models import Booking
from turfs.models import Turf
from accounts.permissions import IsAdmin, IsStaffOrAdmin


class ReviewListCreateView(views.APIView):
    def get_permissions(self):
        return [permissions.AllowAny()]

    def get(self, request):
        turf_id = request.query_params.get("turf_id")
        is_admin = request.user.is_authenticated and (
            request.user.role == "ADMIN" or request.user.is_superuser
        )

        reviews = Review.objects.all().order_by("-created_at")
        if turf_id:
            reviews = reviews.filter(turf_id=turf_id)
        if not is_admin:
            reviews = reviews.filter(is_hidden=False)

        min_rating = request.query_params.get("min_rating")
        if min_rating:
            try:
                reviews = reviews.filter(rating__gte=int(min_rating))
            except (ValueError, TypeError):
                pass

        serializer = ReviewSerializer(reviews, many=True)
        return Response(serializer.data)

    def post(self, request):
        from accounts.settings_helper import BusinessSettingsHelper
        if not BusinessSettingsHelper.is_feature_enabled("REVIEWS"):
            return Response(
                {"error": "Player reviews and ratings are currently disabled by administration."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        booking_id = request.data.get("booking_id")
        turf_id = request.data.get("turf_id") or request.data.get("turf")
        booking = None
        target_turf = None

        if booking_id:
            booking = get_object_or_404(Booking, booking_id=booking_id)
            target_turf = booking.turf
            if request.user.is_authenticated and booking.customer != request.user:
                return Response(
                    {"error": "You can only review your own bookings."},
                    status=status.HTTP_403_FORBIDDEN,
                )
            if booking.reviews.exists():
                return Response(
                    {"error": "You have already reviewed this booking."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
        elif turf_id:
            target_turf = get_object_or_404(Turf, pk=turf_id)
        else:
            target_turf = Turf.objects.filter(is_active=True).first()
            if not target_turf:
                target_turf = get_object_or_404(Turf, pk=1)

        rating = request.data.get("rating", 5)
        try:
            rating_val = int(rating)
            if rating_val < 1 or rating_val > 5:
                return Response(
                    {"error": "Rating must be between 1 and 5 stars."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
        except (ValueError, TypeError):
            return Response(
                {"error": "Rating must be an integer between 1 and 5."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        review_text = request.data.get("review_text", "").strip()
        if not review_text:
            return Response(
                {"error": "Please write a brief review of your match experience."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        serializer = ReviewSerializer(data=request.data)
        if serializer.is_valid():
            customer_user = request.user if request.user.is_authenticated else None
            guest_name = request.data.get("guest_name", "").strip() or (
                (customer_user.full_name or customer_user.email.split("@")[0]) if customer_user else "Verified Athlete"
            )
            guest_phone = request.data.get("guest_phone", "").strip() or (
                customer_user.phone if customer_user else ""
            )

            review = serializer.save(
                customer=customer_user,
                turf=target_turf,
                booking=booking,
                guest_name=guest_name,
                guest_phone=guest_phone,
            )
            # Update turf average rating
            avg_rating = Review.objects.filter(
                turf=target_turf, is_hidden=False
            ).aggregate(Avg("rating"))["rating__avg"]
            count = Review.objects.filter(turf=target_turf, is_hidden=False).count()
            if avg_rating:
                target_turf.rating = round(avg_rating, 2)
                target_turf.total_reviews = count
                target_turf.save()

            return Response(
                ReviewSerializer(review).data, status=status.HTTP_201_CREATED
            )
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


class ReviewDetailView(generics.RetrieveUpdateDestroyAPIView):
    queryset = Review.objects.all()
    serializer_class = ReviewSerializer
    permission_classes = [IsStaffOrAdmin]


class ReviewAnalyticsView(views.APIView):
    permission_classes = [IsStaffOrAdmin]

    def get(self, request):
        reviews = Review.objects.all()
        avg_overall = reviews.aggregate(Avg("rating"))["rating__avg"] or 5.0
        avg_facility = (
            reviews.aggregate(Avg("facility_rating"))["facility_rating__avg"] or 5.0
        )
        avg_staff = reviews.aggregate(Avg("staff_rating"))["staff_rating__avg"] or 5.0

        return Response(
            {
                "total_reviews": reviews.count(),
                "average_rating": round(avg_overall, 2),
                "facility_rating": round(avg_facility, 2),
                "staff_rating": round(avg_staff, 2),
                "flagged_count": reviews.filter(is_flagged=True).count(),
            }
        )
