import uuid
from decimal import Decimal
from rest_framework import status, views, permissions
from rest_framework.response import Response
from django.shortcuts import get_object_or_404

from .models import WalletTransaction
from .serializers import WalletTransactionSerializer
from accounts.models import User
from accounts.permissions import IsAdmin
from notifications.models import Notification
from accounts.settings_helper import BusinessSettingsHelper


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


from django.db import transaction
from django.utils import timezone
from payments.razorpay_client import RazorpayService
from payments.models import Payment
from audit.models import AuditLog


class WalletCreateRazorpayOrderView(views.APIView):
    """
    Creates a Razorpay Order specifically for funding customer wallet balance.
    """
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        amount_input = request.data.get("amount")
        if not amount_input:
            return Response(
                {"error": "Amount is required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        min_topup = Decimal(str(BusinessSettingsHelper.get_payment_settings().get("minTopUpAmount", 10.0)))
        try:
            amount = Decimal(str(amount_input))
            if amount < min_topup:
                return Response(
                    {"error": f"Minimum top-up amount is ₹{min_topup:.0f}."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
        except Exception:
            return Response(
                {"error": "Invalid top-up amount."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        receipt_id = f"WTOP-{uuid.uuid4().hex[:8].upper()}"
        try:
            rzp_order = RazorpayService.create_order(
                amount_in_rupees=amount,
                receipt_id=receipt_id,
                notes={
                    "type": "WALLET_TOPUP",
                    "customer_id": str(request.user.id),
                    "customer_email": request.user.email,
                },
            )
        except Exception as e:
            return Response(
                {"error": f"Failed to initialize wallet payment: {str(e)}"},
                status=status.HTTP_502_BAD_GATEWAY,
            )

        payment_id = Payment.generate_payment_id()
        txn_ref = f"TXN-{uuid.uuid4().hex[:12].upper()}"

        payment = Payment.objects.create(
            payment_id=payment_id,
            customer=request.user,
            provider="RAZORPAY",
            provider_order_id=rzp_order["order_id"],
            amount=amount,
            currency="INR",
            payment_method="UPI",
            payment_type="FULL",
            transaction_reference=txn_ref,
            status="PENDING",
            notes=f"Wallet Top-Up of ₹{amount}",
        )

        AuditLog.objects.create(
            user=request.user,
            action="WALLET_TOPUP_INITIATED",
            resource_type="PAYMENT",
            resource_id=payment.payment_id,
            details={
                "order_id": rzp_order["order_id"],
                "amount": float(amount),
            },
        )

        return Response(
            {
                "order_id": rzp_order["order_id"],
                "amount": rzp_order["amount"],
                "currency": rzp_order["currency"],
                "key_id": rzp_order["key_id"],
                "payment_id": payment.payment_id,
                "amount_in_rupees": float(amount),
            },
            status=status.HTTP_201_CREATED,
        )


class WalletVerifyRazorpayPaymentView(views.APIView):
    """
    Verifies Razorpay signature and atomically credits customer wallet balance.
    """
    permission_classes = [permissions.IsAuthenticated]

    @transaction.atomic
    def post(self, request):
        razorpay_order_id = request.data.get("razorpay_order_id")
        razorpay_payment_id = request.data.get("razorpay_payment_id")
        razorpay_signature = request.data.get("razorpay_signature")

        if not razorpay_order_id or not razorpay_payment_id:
            return Response(
                {"error": "razorpay_order_id and razorpay_payment_id are required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        payment = Payment.objects.select_for_update().filter(
            customer=request.user, provider_order_id=razorpay_order_id
        ).first()

        if not payment:
            return Response(
                {"error": "No pending wallet payment found for this order."},
                status=status.HTTP_404_NOT_FOUND,
            )

        # Idempotency check
        if payment.status in ("PAID", "SUCCESSFUL"):
            prof = getattr(request.user, "customer_profile", None)
            return Response(
                {
                    "status": "SUCCESS",
                    "message": "Payment was already verified.",
                    "wallet_balance": float(prof.wallet_balance if prof else 0.0),
                },
                status=status.HTTP_200_OK,
            )

        # Verify signature
        is_valid = RazorpayService.verify_payment_signature(
            razorpay_order_id=razorpay_order_id,
            razorpay_payment_id=razorpay_payment_id,
            razorpay_signature=razorpay_signature,
        )

        if not is_valid:
            payment.status = "FAILED"
            payment.failure_reason = "Signature verification failed."
            payment.save()
            return Response(
                {"error": "Payment verification failed."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        now = timezone.now()
        payment.status = "PAID"
        payment.provider_payment_id = razorpay_payment_id
        payment.provider_signature = razorpay_signature or ""
        payment.paid_at = now
        payment.completed_at = now
        payment.save()

        # Credit customer wallet
        profile = getattr(request.user, "customer_profile", None)
        if not profile:
            return Response(
                {"error": "Customer profile not found."},
                status=status.HTTP_404_NOT_FOUND,
            )

        profile.wallet_balance += payment.amount
        profile.save()

        txn_ref = f"TOPUP-{uuid.uuid4().hex[:8].upper()}"
        txn = WalletTransaction.objects.create(
            customer=request.user,
            amount=payment.amount,
            transaction_type="CREDIT",
            source="TOP_UP",
            reference_id=txn_ref,
            description=f"Razorpay Wallet Top-Up (Ref: {razorpay_payment_id})",
            balance_after=profile.wallet_balance,
        )

        Notification.objects.create(
            user=request.user,
            notification_type="SYSTEM_ALERT",
            title=f"Wallet Credited: +₹{payment.amount}",
            message=f"₹{payment.amount} has been securely added to your Turf Cash Wallet via Razorpay.",
            data={"payment_id": payment.payment_id, "amount": float(payment.amount)},
        )

        AuditLog.objects.create(
            user=request.user,
            action="WALLET_TOPUP_VERIFIED",
            resource_type="WALLET",
            resource_id=txn_ref,
            details={
                "amount": float(payment.amount),
                "new_balance": float(profile.wallet_balance),
                "payment_id": payment.payment_id,
            },
        )

        return Response(
            {
                "status": "SUCCESS",
                "message": f"₹{payment.amount} successfully added to your wallet!",
                "wallet_balance": float(profile.wallet_balance),
                "transaction": WalletTransactionSerializer(txn).data,
            },
            status=status.HTTP_200_OK,
        )


class WalletTopUpView(views.APIView):
    """
    Direct wallet balance adjustment endpoint (Restricted to Admin for balance management).
    """
    permission_classes = [IsAdmin]

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
