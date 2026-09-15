from rest_framework import status, views, permissions
from rest_framework.response import Response
from django.shortcuts import get_object_or_404

from .models import Payment, Refund
from bookings.models import Booking
from .serializers import PaymentSerializer, RefundSerializer
from .gateway import MockPaymentGateway
from accounts.permissions import IsAdmin, IsStaffOrAdmin


class PaymentListCreateView(views.APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        if request.user.role in ("STAFF", "ADMIN") or request.user.is_superuser:
            payments = Payment.objects.all().select_related("customer", "booking")
            booking_id = request.query_params.get("booking_id")
            if booking_id:
                payments = payments.filter(booking__booking_id=booking_id)
        else:
            payments = Payment.objects.filter(customer=request.user).select_related(
                "booking"
            )
        return Response(PaymentSerializer(payments, many=True).data)

    def post(self, request):
        booking_id = request.data.get("booking_id")
        amount = request.data.get("amount")
        payment_method = request.data.get("payment_method", "UPI")
        payment_type = request.data.get("payment_type", "FULL")
        simulate_outcome = request.data.get("simulate_outcome", "SUCCESS")

        booking = get_object_or_404(Booking, booking_id=booking_id)
        if request.user.role == "CUSTOMER" and booking.customer != request.user:
            return Response(
                {"error": "Unauthorized."}, status=status.HTTP_403_FORBIDDEN
            )

        try:
            payment, success, msg = MockPaymentGateway.process_payment(
                booking=booking,
                customer=request.user,
                amount=amount or booking.balance_due or booking.final_amount,
                payment_method=payment_method,
                payment_type=payment_type,
                simulate_outcome=simulate_outcome,
            )
            serializer = PaymentSerializer(payment)
            if success:
                return Response(
                    {"message": msg, "payment": serializer.data},
                    status=status.HTTP_201_CREATED,
                )
            else:
                return Response(
                    {"error": msg, "payment": serializer.data},
                    status=status.HTTP_400_BAD_REQUEST,
                )
        except ValueError as e:
            return Response({"error": str(e)}, status=status.HTTP_400_BAD_REQUEST)


class ProcessRefundView(views.APIView):
    permission_classes = [IsAdmin]

    def post(self, request, pk):
        payment = get_object_or_404(Payment, pk=pk)
        amount = request.data.get("amount")
        refund_to = request.data.get("refund_to", "WALLET")
        reason = request.data.get("reason", "Admin initiated refund")

        refund = MockPaymentGateway.process_refund(
            payment=payment, amount=amount, refund_to=refund_to, reason=reason
        )
        return Response(RefundSerializer(refund).data, status=status.HTTP_201_CREATED)


class RefundListView(views.APIView):
    permission_classes = [IsStaffOrAdmin]

    def get(self, request):
        refunds = Refund.objects.all().select_related("payment", "booking")
        return Response(RefundSerializer(refunds, many=True).data)
