"""
Friends Turf Payment Gateway Service.
Delegates to RazorpayService for cryptographic order creation, signature verification, and refunds.
"""

from .razorpay_client import RazorpayService


class PaymentGatewayService:
    @classmethod
    def create_order(cls, amount_in_rupees, receipt_id, notes=None):
        return RazorpayService.create_order(amount_in_rupees, receipt_id, notes)

    @classmethod
    def verify_payment(cls, razorpay_order_id, razorpay_payment_id, razorpay_signature):
        return RazorpayService.verify_payment_signature(
            razorpay_order_id, razorpay_payment_id, razorpay_signature
        )

    @classmethod
    def initiate_refund(cls, razorpay_payment_id, amount_in_rupees=None, notes=None):
        return RazorpayService.initiate_refund(
            razorpay_payment_id, amount_in_rupees, notes
        )
