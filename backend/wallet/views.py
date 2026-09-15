import uuid
from decimal import Decimal
from rest_framework import status, views, permissions
from rest_framework.response import Response
from django.shortcuts import get_object_or_404

from .models import WalletTransaction, LoyaltyTransaction
from .serializers import WalletTransactionSerializer, LoyaltyTransactionSerializer
from accounts.models import User
from accounts.permissions import IsAdmin
from notifications.models import Notification


class WalletDetailView(views.APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        profile = getattr(request.user, "customer_profile", None)
        balance = profile.wallet_balance if profile else Decimal("0.00")
        transactions = WalletTransaction.objects.filter(customer=request.user).order_by(
            "-created_at"
        )[:30]

        return Response(
            {
                "wallet_balance": float(balance),
                "transactions": WalletTransactionSerializer(
                    transactions, many=True
                ).data,
            }
        )


class WalletTopUpView(views.APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        amount = Decimal(str(request.data.get("amount", "0.00")))
        if amount <= Decimal("0.00"):
            return Response(
                {"error": "Amount must be greater than zero."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        profile = getattr(request.user, "customer_profile", None)
        if not profile:
            return Response(
                {"error": "Customer profile not found."},
                status=status.HTTP_404_NOT_FOUND,
            )

        profile.wallet_balance += amount
        profile.save()

        txn_ref = f"TOPUP-{uuid.uuid4().hex[:8].upper()}"
        txn = WalletTransaction.objects.create(
            customer=request.user,
            amount=amount,
            transaction_type="CREDIT",
            source="TOP_UP",
            reference_id=txn_ref,
            description=f"Wallet top-up of ₹{amount}",
            balance_after=profile.wallet_balance,
        )

        return Response(
            {
                "message": f"₹{amount} added to your wallet successfully!",
                "wallet_balance": float(profile.wallet_balance),
                "transaction": WalletTransactionSerializer(txn).data,
            }
        )


class LoyaltyDetailView(views.APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        profile = getattr(request.user, "customer_profile", None)
        points = profile.loyalty_points if profile else 0
        transactions = LoyaltyTransaction.objects.filter(
            customer=request.user
        ).order_by("-created_at")[:30]

        return Response(
            {
                "loyalty_points": points,
                "points_value_in_inr": points * 1.0,  # 1 point = ₹1
                "transactions": LoyaltyTransactionSerializer(
                    transactions, many=True
                ).data,
            }
        )


class LoyaltyRedeemView(views.APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        points_to_redeem = int(request.data.get("points", 0))
        if points_to_redeem < 100:
            return Response(
                {"error": "Minimum 100 points required to redeem."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        profile = getattr(request.user, "customer_profile", None)
        if not profile or profile.loyalty_points < points_to_redeem:
            return Response(
                {"error": "Insufficient loyalty points balance."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # 1 point = ₹1
        cash_value = Decimal(str(points_to_redeem))
        profile.loyalty_points -= points_to_redeem
        profile.wallet_balance += cash_value
        profile.save()

        ref = f"REDM-{uuid.uuid4().hex[:6].upper()}"
        LoyaltyTransaction.objects.create(
            customer=request.user,
            points=points_to_redeem,
            transaction_type="REDEEM",
            source="PROMOTION",
            reference_id=ref,
            description=f"Redeemed {points_to_redeem} points to wallet",
            balance_after=profile.loyalty_points,
        )

        WalletTransaction.objects.create(
            customer=request.user,
            amount=cash_value,
            transaction_type="CREDIT",
            source="PROMOTIONAL",
            reference_id=ref,
            description=f"Loyalty points conversion: {points_to_redeem} pts -> ₹{cash_value}",
            balance_after=profile.wallet_balance,
        )

        return Response(
            {
                "message": f"Redeemed {points_to_redeem} points into ₹{cash_value} wallet cash!",
                "wallet_balance": float(profile.wallet_balance),
                "loyalty_points": profile.loyalty_points,
            }
        )


class AdminAdjustWalletView(views.APIView):
    permission_classes = [IsAdmin]

    def post(self, request):
        user_id = request.data.get("user_id")
        amount = Decimal(str(request.data.get("amount", "0.00")))
        adjustment_type = request.data.get("type", "CREDIT")  # CREDIT or DEBIT
        reason = request.data.get("reason", "Admin manual adjustment")

        user = get_object_or_404(User, pk=user_id)
        if not hasattr(user, "customer_profile"):
            return Response(
                {"error": "Customer profile not found."},
                status=status.HTTP_404_NOT_FOUND,
            )

        prof = user.customer_profile
        if adjustment_type == "CREDIT":
            prof.wallet_balance += amount
        else:
            prof.wallet_balance = max(Decimal("0.00"), prof.wallet_balance - amount)
        prof.save()

        ref = f"ADM-{uuid.uuid4().hex[:6].upper()}"
        WalletTransaction.objects.create(
            customer=user,
            amount=amount,
            transaction_type=adjustment_type,
            source="ADMIN",
            reference_id=ref,
            description=reason,
            balance_after=prof.wallet_balance,
        )

        Notification.objects.create(
            user=user,
            notification_type="SYSTEM_ALERT",
            title=f"Wallet Adjusted: {'+' if adjustment_type == 'CREDIT' else '-'}₹{amount}",
            message=f"Your wallet has been updated: {reason}. New balance: ₹{prof.wallet_balance}",
        )

        return Response(
            {
                "message": "Wallet adjusted successfully",
                "wallet_balance": float(prof.wallet_balance),
            }
        )
