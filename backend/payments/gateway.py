import uuid
from decimal import Decimal
from django.utils import timezone
from .models import Payment, Refund
from bookings.models import Booking
from wallet.models import WalletTransaction
from notifications.models import Notification
from audit.models import AuditLog


class MockPaymentGateway:
    """
    Realistic Mock Payment Gateway supporting:
    - Card, UPI, Net Banking, Turf Wallet, Cash
    - Full, Advance (Partial), and Balance clearance
    - Immediate success simulation or simulated timeout/failure
    - Full and Partial Refunds to Wallet or Original source
    """

    @classmethod
    def process_payment(
        cls,
        booking,
        customer,
        amount,
        payment_method,
        payment_type="FULL",
        simulate_outcome="SUCCESS",
    ):
        """
        Executes payment processing against booking.
        """
        amount = Decimal(str(amount))
        payment_id = Payment.generate_payment_id()
        txn_ref = f"TXN-{uuid.uuid4().hex[:14].upper()}"

        # If user chose WALLET payment
        if payment_method == "WALLET":
            if (
                not hasattr(customer, "customer_profile")
                or customer.customer_profile.wallet_balance < amount
            ):
                raise ValueError("Insufficient wallet balance.")

            # Deduct from wallet
            prof = customer.customer_profile
            prof.wallet_balance -= amount
            prof.save()

            WalletTransaction.objects.create(
                customer=customer,
                amount=amount,
                transaction_type="DEBIT",
                source="REDEMPTION",
                reference_id=booking.booking_id,
                description=f"Paid for booking {booking.booking_id}",
                balance_after=prof.wallet_balance,
            )

        if simulate_outcome == "TIMEOUT":
            payment = Payment.objects.create(
                payment_id=payment_id,
                booking=booking,
                customer=customer,
                amount=amount,
                payment_method=payment_method,
                payment_type=payment_type,
                transaction_reference=txn_ref,
                status="TIMEOUT",
            )
            return payment, False, "Payment gateway timed out. Please try again."

        elif simulate_outcome == "FAILED":
            payment = Payment.objects.create(
                payment_id=payment_id,
                booking=booking,
                customer=customer,
                amount=amount,
                payment_method=payment_method,
                payment_type=payment_type,
                transaction_reference=txn_ref,
                status="FAILED",
            )
            return (
                payment,
                False,
                "Card declined or transaction failed by issuing bank.",
            )

        # Successful payment
        now = timezone.now()
        payment = Payment.objects.create(
            payment_id=payment_id,
            booking=booking,
            customer=customer,
            amount=amount,
            payment_method=payment_method,
            payment_type=payment_type,
            transaction_reference=txn_ref,
            status="SUCCESSFUL",
            gateway_response={
                "gateway": "FT_MockPay_v2",
                "code": "00",
                "auth_code": uuid.uuid4().hex[:6].upper(),
            },
            completed_at=now,
        )

        # Update booking
        booking.amount_paid += amount
        booking.balance_due = max(
            Decimal("0.00"), booking.final_amount - booking.amount_paid
        )
        if booking.balance_due == Decimal("0.00"):
            booking.status = "CONFIRMED"
        booking.save()

        Notification.objects.create(
            user=customer,
            notification_type="PAYMENT_SUCCESS",
            title=f"Payment Successful (₹{amount})",
            message=f"Received payment of ₹{amount} for booking {booking.booking_id}. Txn Ref: {txn_ref}",
            data={"payment_id": payment_id, "booking_id": booking.booking_id},
        )

        AuditLog.objects.create(
            user=customer,
            action="PAYMENT_COMPLETED",
            resource_type="PAYMENT",
            resource_id=payment_id,
            details={
                "amount": float(amount),
                "booking_id": booking.booking_id,
                "method": payment_method,
            },
        )

        return payment, True, "Payment verified successfully!"

    @classmethod
    def process_refund(
        cls, payment, amount=None, refund_to="WALLET", reason="Booking Cancelled"
    ):
        amount = Decimal(str(amount)) if amount else payment.amount
        refund_id = f"REF-{uuid.uuid4().hex[:8].upper()}"

        refund = Refund.objects.create(
            refund_id=refund_id,
            payment=payment,
            booking=payment.booking,
            amount=amount,
            refund_type="FULL" if amount >= payment.amount else "PARTIAL",
            refund_to=refund_to,
            status="COMPLETED",
            reason=reason,
            reference_id=f"RREF-{uuid.uuid4().hex[:10].upper()}",
            completed_at=timezone.now(),
        )

        # If refund to wallet, credit immediately
        if refund_to == "WALLET" and hasattr(payment.customer, "customer_profile"):
            prof = payment.customer.customer_profile
            prof.wallet_balance += amount
            prof.save()

            WalletTransaction.objects.create(
                customer=payment.customer,
                amount=amount,
                transaction_type="CREDIT",
                source="REFUND",
                reference_id=refund_id,
                description=f"Refund for payment {payment.payment_id}",
                balance_after=prof.wallet_balance,
            )

        Notification.objects.create(
            user=payment.customer,
            notification_type="REFUND_PROCESSED",
            title=f"Refund Processed (₹{amount})",
            message=f"Refund of ₹{amount} for booking {payment.booking.booking_id} has been processed to {refund_to}.",
            data={"refund_id": refund_id},
        )

        return refund
