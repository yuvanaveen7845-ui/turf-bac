from rest_framework import generics, permissions
from .models import PricingRule, Holiday, SpecialEvent
from .serializers import (
    PricingRuleSerializer,
    HolidaySerializer,
    SpecialEventSerializer,
)
from accounts.permissions import IsAdmin, IsStaffOrAdmin


class PricingRuleListCreateView(generics.ListCreateAPIView):
    queryset = PricingRule.objects.all().order_by("-priority")
    serializer_class = PricingRuleSerializer

    def get_permissions(self):
        if self.request.method == "GET":
            return [permissions.AllowAny()]
        return [IsAdmin()]


class PricingRuleDetailView(generics.RetrieveUpdateDestroyAPIView):
    queryset = PricingRule.objects.all()
    serializer_class = PricingRuleSerializer
    permission_classes = [IsAdmin]


class HolidayListCreateView(generics.ListCreateAPIView):
    queryset = Holiday.objects.all().order_by("date")
    serializer_class = HolidaySerializer

    def get_permissions(self):
        if self.request.method == "GET":
            return [permissions.AllowAny()]
        return [IsAdmin()]


class SpecialEventListCreateView(generics.ListCreateAPIView):
    queryset = SpecialEvent.objects.all().order_by("date")
    serializer_class = SpecialEventSerializer

    def get_permissions(self):
        if self.request.method == "GET":
            return [permissions.AllowAny()]
        return [IsAdmin()]
