from rest_framework import status, views, permissions
from rest_framework.response import Response
from django.shortcuts import get_object_or_404

from .models import QRTicket, CheckInRecord
from .services import QRService
from bookings.models import Booking
from accounts.permissions import IsStaffOrAdmin


class QRValidateScanView(views.APIView):
    permission_classes = [IsStaffOrAdmin]

    def post(self, request):
        raw_token = request.data.get("qr_data", "").strip()
        notes = request.data.get("notes", "")

        if not raw_token:
            return Response(
                {"error": "No QR code or booking token provided."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        result = QRService.validate_and_checkin(
            raw_data=raw_token, staff_user=request.user, notes=notes
        )

        return Response(result, status=status.HTTP_200_OK)


class GetBookingQRView(views.APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, booking_id):
        booking = get_object_or_404(Booking, booking_id=booking_id)
        if request.user.role == "CUSTOMER" and booking.customer != request.user:
            return Response(
                {"error": "Unauthorized."}, status=status.HTTP_403_FORBIDDEN
            )

        ticket = getattr(booking, "qr_ticket", None)
        if not ticket:
            ticket = QRService.generate_qr_for_booking(booking)

        return Response(
            {
                "booking_id": booking.booking_id,
                "ticket_code": ticket.ticket_code,
                "qr_base64": ticket.qr_base64,
                "is_used": ticket.is_used,
                "used_at": ticket.used_at,
                "turf_name": booking.turf.name,
                "date": str(booking.date),
                "time": f"{booking.start_time.strftime('%H:%M')} - {booking.end_time.strftime('%H:%M')}",
                "customer_name": booking.customer.full_name,
                "status": booking.status,
            }
        )


class CheckInHistoryView(views.APIView):
    permission_classes = [IsStaffOrAdmin]

    def get(self, request):
        logs = (
            CheckInRecord.objects.all()
            .select_related("booking", "scanned_by")
            .order_by("-scanned_at")[:50]
        )
        data = [
            {
                "id": str(log.id),
                "booking_id": log.booking.booking_id,
                "customer_name": log.booking.customer.full_name,
                "turf_name": log.booking.turf.name,
                "scanned_by": log.scanned_by.email,
                "scanned_at": log.scanned_at.strftime("%d %b %Y, %I:%M %p"),
                "result": log.result,
                "message": log.message,
                "notes": log.notes,
            }
            for log in logs
        ]
        return Response(data)
