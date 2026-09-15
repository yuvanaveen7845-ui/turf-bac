from datetime import timedelta
from rest_framework import status, views, generics, permissions
from rest_framework.response import Response
from django.shortcuts import get_object_or_404
from django.utils import timezone

from .models import MembershipPlan, CustomerMembership
from .serializers import MembershipPlanSerializer, CustomerMembershipSerializer
from accounts.permissions import IsAdmin
from notifications.models import Notification


class MembershipPlanListView(generics.ListCreateAPIView):
    serializer_class = MembershipPlanSerializer

    def get_queryset(self):
        if self.request.user.is_authenticated and (
            self.request.user.role == "ADMIN" or self.request.user.is_superuser
        ):
            return MembershipPlan.objects.all().order_by("tier_level")
        return MembershipPlan.objects.filter(is_active=True).order_by("tier_level")

    def get_permissions(self):
        if self.request.method == "GET":
            return [permissions.AllowAny()]
        return [IsAdmin()]


class CustomerMembershipView(views.APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        active_sub = CustomerMembership.objects.filter(
            customer=request.user, status="ACTIVE"
        ).first()
        if not active_sub:
            return Response({"has_active_membership": False, "membership": None})
        return Response(
            {
                "has_active_membership": True,
                "membership": CustomerMembershipSerializer(active_sub).data,
            }
        )

    def post(self, request):
        plan_id = request.data.get("plan_id")
        billing_cycle = request.data.get("billing_cycle", "MONTHLY")
        plan = get_object_or_404(MembershipPlan, pk=plan_id)

        now = timezone.now().date()
        duration = 365 if billing_cycle == "ANNUAL" else 30
        end_date = now + timedelta(days=duration)

        # Cancel any previous active membership
        CustomerMembership.objects.filter(
            customer=request.user, status="ACTIVE"
        ).update(status="CANCELLED")

        sub = CustomerMembership.objects.create(
            customer=request.user,
            plan=plan,
            billing_cycle=billing_cycle,
            start_date=now,
            end_date=end_date,
            status="ACTIVE",
        )

        # Update customer profile tier
        if hasattr(request.user, "customer_profile"):
            prof = request.user.customer_profile
            prof.membership_tier = plan.name.upper()
            prof.save()

        Notification.objects.create(
            user=request.user,
            notification_type="MEMBERSHIP_ALERT",
            title=f"Welcome to {plan.name} Club!",
            message=f"You now enjoy {plan.discount_percentage}% discount on every booking until {end_date.strftime('%d %b %Y')}.",
        )

        return Response(
            CustomerMembershipSerializer(sub).data, status=status.HTTP_201_CREATED
        )
