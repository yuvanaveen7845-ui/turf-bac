from rest_framework import status, views, permissions
from rest_framework.response import Response
from django.shortcuts import get_object_or_404
from .models import Notification
from .serializers import NotificationSerializer


class NotificationListView(views.APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        notifications = Notification.objects.filter(user=request.user).order_by(
            "-created_at"
        )[:40]
        unread_count = Notification.objects.filter(
            user=request.user, is_read=False
        ).count()
        return Response(
            {
                "unread_count": unread_count,
                "notifications": NotificationSerializer(notifications, many=True).data,
            }
        )

    def post(self, request):
        # Mark all as read
        Notification.objects.filter(user=request.user, is_read=False).update(
            is_read=True
        )
        return Response({"message": "All notifications marked as read."})


class NotificationDetailView(views.APIView):
    permission_classes = [permissions.IsAuthenticated]

    def put(self, request, pk):
        notif = get_object_or_404(Notification, pk=pk, user=request.user)
        notif.is_read = True
        notif.save()
        return Response(NotificationSerializer(notif).data)


class SendMatchPassEmailView(views.APIView):
    """
    API endpoint to dispatch official digital Match Pass via Django SMTP.
    Supports logged-in user email or explicit email override.
    """
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        booking_id = (request.data.get("booking_id") or "").strip()
        custom_email = (request.data.get("email") or "").strip()

        if not booking_id:
            return Response(
                {"error": "booking_id is required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        from bookings.models import Booking
        from .services import EmailNotificationService

        booking = get_object_or_404(Booking, booking_id=booking_id)

        # Access check: Only booking owner or staff/admin can dispatch pass
        if request.user.role == "CUSTOMER" and booking.customer != request.user:
            return Response(
                {"error": "You are not authorized to access this match pass."},
                status=status.HTTP_403_FORBIDDEN,
            )

        target_email = custom_email or getattr(booking.customer, "email", "")
        if not target_email:
            return Response(
                {"error": "No recipient email address provided or found on booking."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Send match pass email
        success = EmailNotificationService.send_booking_confirmation_email(
            booking=booking,
            recipient_email=target_email,
        )

        if success:
            return Response(
                {
                    "status": "SUCCESS",
                    "message": f"Match pass successfully emailed to {target_email}.",
                    "booking_id": booking.booking_id,
                    "recipient": target_email,
                },
                status=status.HTTP_200_OK,
            )
        else:
            return Response(
                {
                    "status": "FAILED",
                    "error": f"Failed to send email to {target_email}. Please verify SMTP settings.",
                },
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

