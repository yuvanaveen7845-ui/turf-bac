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
        if self.request.method == "GET":
            return [permissions.AllowAny()]
        return [permissions.IsAuthenticated()]

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
        booking = get_object_or_404(Booking, booking_id=booking_id)

        if booking.customer != request.user:
            return Response(
                {"error": "You can only review your own bookings."},
                status=status.HTTP_403_FORBIDDEN,
            )

        if hasattr(booking, "review"):
            return Response(
                {"error": "You have already reviewed this booking."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        serializer = ReviewSerializer(data=request.data)
        if serializer.is_valid():
            review = serializer.save(
                customer=request.user, turf=booking.turf, booking=booking
            )
            # Update turf average rating
            avg_rating = Review.objects.filter(
                turf=booking.turf, is_hidden=False
            ).aggregate(Avg("rating"))["rating__avg"]
            count = Review.objects.filter(turf=booking.turf, is_hidden=False).count()
            if avg_rating:
                booking.turf.rating = round(avg_rating, 2)
                booking.turf.total_reviews = count
                booking.turf.save()

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
