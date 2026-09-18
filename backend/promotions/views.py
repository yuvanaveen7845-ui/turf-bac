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
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        code = request.data.get("code", "").strip().upper()
        amount = Decimal(str(request.data.get("amount", "0.00")))

        if not code:
            return Response(
                {"error": "Coupon code required."}, status=status.HTTP_400_BAD_REQUEST
            )

        coupon = Coupon.objects.filter(code=code).first()
        if not coupon:
            return Response(
                {"is_valid": False, "message": "Invalid coupon code."},
                status=status.HTTP_404_NOT_FOUND,
            )

        valid, msg = coupon.is_valid_for_user(request.user, amount)
        if not valid:
            return Response(
                {"is_valid": False, "message": msg}, status=status.HTTP_400_BAD_REQUEST
            )

        discount = coupon.calculate_discount(amount)
        return Response(
            {
                "is_valid": True,
                "code": coupon.code,
                "title": coupon.title,
                "discount_type": coupon.discount_type,
                "discount_value": float(coupon.discount_value),
                "discount_amount": float(discount),
                "final_amount": float(max(Decimal("0.00"), amount - discount)),
                "message": f"Coupon applied! You save ₹{discount}.",
            }
        )
