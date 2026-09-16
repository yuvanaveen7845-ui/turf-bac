import hmac
import hashlib
import logging
import razorpay
from django.conf import settings

logger = logging.getLogger(__name__)


class RazorpayService:
    """
    Razorpay Server Integration Service.
    Handles order generation, HMAC-SHA256 signature verification, webhook validation, and refunds.
    """

    @classmethod
    def get_key_id(cls):
        return getattr(settings, "RAZORPAY_KEY_ID", "")

    @classmethod
    def get_key_secret(cls):
        return getattr(settings, "RAZORPAY_KEY_SECRET", "")

    @classmethod
    def get_webhook_secret(cls):
        return getattr(settings, "RAZORPAY_WEBHOOK_SECRET", "")

    @classmethod
    def get_client(cls):
        key_id = cls.get_key_id()
        key_secret = cls.get_key_secret()
        return razorpay.Client(auth=(key_id, key_secret))

    @classmethod
    def create_order(cls, amount_in_rupees, receipt_id, notes=None, currency="INR"):
        """
        Creates a Razorpay Order server-side.
        Amount must be provided in Rupees, converted to Paise (x 100).
        """
        amount_paise = int(round(float(amount_in_rupees) * 100))
        key_id = cls.get_key_id()
        key_secret = cls.get_key_secret()

        client = cls.get_client()
        order_payload = {
            "amount": amount_paise,
            "currency": currency,
            "receipt": str(receipt_id),
            "notes": notes or {},
            "payment_capture": 1,
        }

        try:
            order = client.order.create(data=order_payload)
            return {
                "order_id": order["id"],
                "amount": order["amount"],
                "currency": order["currency"],
                "key_id": key_id,
            }
        except Exception as e:
            logger.error(f"Razorpay Order creation error: {str(e)}")
            raise e

    @classmethod
    def verify_payment_signature(
        cls, razorpay_order_id, razorpay_payment_id, razorpay_signature
    ):
        """
        Cryptographically verifies the Razorpay payment signature via HMAC-SHA256.
        """
        if not razorpay_order_id or not razorpay_payment_id or not razorpay_signature:
            return False

        key_secret = cls.get_key_secret()
        msg = f"{razorpay_order_id}|{razorpay_payment_id}"

        generated_signature = hmac.new(
            key_secret.encode("utf-8"),
            msg.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

        return hmac.compare_digest(generated_signature, razorpay_signature)

    @classmethod
    def verify_webhook_signature(cls, body_bytes, signature_header):
        """
        Verifies Razorpay Webhook signature using RAZORPAY_WEBHOOK_SECRET.
        """
        if not signature_header or not body_bytes:
            return False

        webhook_secret = cls.get_webhook_secret()
        if not webhook_secret:
            return False

        generated_signature = hmac.new(
            webhook_secret.encode("utf-8"),
            body_bytes,
            hashlib.sha256,
        ).hexdigest()

        return hmac.compare_digest(generated_signature, signature_header)

    @classmethod
    def initiate_refund(cls, razorpay_payment_id, amount_in_rupees=None, notes=None):
        """
        Initiates a partial or full refund through Razorpay API.
        """
        client = cls.get_client()
        refund_data = {"notes": notes or {}}
        if amount_in_rupees:
            refund_data["amount"] = int(round(float(amount_in_rupees) * 100))

        try:
            refund = client.payment.refund(razorpay_payment_id, refund_data)
            return {
                "success": True,
                "refund_id": refund.get("id"),
                "status": refund.get("status"),
            }
        except Exception as e:
            logger.error(f"Razorpay Refund failed for payment {razorpay_payment_id}: {str(e)}")
            return {
                "success": False,
                "error": str(e),
            }
