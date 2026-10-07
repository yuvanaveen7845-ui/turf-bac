from decimal import Decimal
from rest_framework import status, views, generics, permissions
from rest_framework.response import Response
from django.shortcuts import get_object_or_404
from django.utils import timezone

from .models import Coupon, CouponUsage
from .serializers import CouponSerializer
from accounts.permissions import IsAdmin


class CouponListCreateView(generics.ListCreateAPIView):
    serializer_class = CouponSerializer

    def get_queryset(self):
        if self.request.user.is_authenticated and (
            self.request.user.role == "ADMIN" or self.request.user.is_superuser
        ):
            return Coupon.objects.all().order_by("-created_at")
        # Public offers
        today = timezone.now().date()
        return Coupon.objects.filter(
            is_active=True, start_date__lte=today, end_date__gte=today
        )

    def get_permissions(self):
        if self.request.method == "GET":
            return [permissions.AllowAny()]
        return [IsAdmin()]


class CouponDetailView(generics.RetrieveUpdateDestroyAPIView):
    queryset = Coupon.objects.all()
    serializer_class = CouponSerializer
    permission_classes = [IsAdmin]


class ValidateCouponView(views.APIView):
    permission_classes = [permissions.AllowAny]

    def post(self, request):
        return Response(
            {"is_valid": False, "message": "Coupon system has been retired."},
            status=status.HTTP_400_BAD_REQUEST,
        )
