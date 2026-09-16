from rest_framework import status, views, permissions
from rest_framework.response import Response
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.db.models import Count, Q

from .models import QRCredential, CheckIn
from .services import QRService
from bookings.models import Booking
from accounts.permissions import IsStaffOrAdmin, IsAdmin
from realtime.events import publish_event


class QRValidateScanView(views.APIView):
    """
    Primary gate scanner admission endpoint for staff operators.
    """
    permission_classes = [IsStaffOrAdmin]

    def post(self, request):
        raw_token = request.data.get("qr_data", "").strip()
        method = request.data.get("method", "QR_SCAN")
        facility_id = request.data.get("facility_id", None)
        device_id = request.data.get("device_identifier", "")

        if not raw_token:
            return Response(
                {"error": "No QR code or booking token provided."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        result = QRService.evaluate_and_checkin(
            raw_input=raw_token,
            staff_user=request.user,
            method=method,
            facility_id=facility_id,
            device_identifier=device_id,
        )

        if result.get("status") == "ADMITTED":
            publish_event(
                channel="gate",
                event_type="GATE_CHECK_IN",
                payload={
                    "booking_id": result.get("booking_id"),
                    "result": "VALID",
                    "customer_name": result.get("customer", {}).get("name") or "Player",
                    "turf_name": result.get("turf", {}).get("name") or "Pitch",
                    "scanned_at": timezone.now().strftime("%I:%M %p"),
                    "scanned_by": request.user.first_name or request.user.email,
                },
            )
            publish_event(
                channel="operations",
                event_type="OPERATIONS_UPDATE",
                payload={
                    "type": "GATE_ADMISSION",
                    "booking_id": result.get("booking_id"),
                },
            )

        return Response(result, status=status.HTTP_200_OK)


class QRManualOverrideView(views.APIView):
    """
    Admin manual override admission endpoint with required reason and audit.
    """
    permission_classes = [IsStaffOrAdmin]

    def post(self, request):
        booking_id = request.data.get("booking_id", "").strip()
        override_reason = request.data.get("override_reason", "").strip()
        facility_id = request.data.get("facility_id", None)

        if not booking_id:
            return Response(
                {"error": "Booking ID is required for manual override."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if not override_reason:
            return Response(
                {"error": "An explicit reason is required to execute a gate override."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        result = QRService.evaluate_and_checkin(
            raw_input=booking_id,
            staff_user=request.user,
            method="ADMIN_OVERRIDE",
            facility_id=facility_id,
            override_reason=override_reason,
            is_override=True,
            device_identifier=f"Override by {request.user.email}",
        )

        if result.get("status") == "ADMITTED":
            publish_event(
                channel="gate",
                event_type="GATE_CHECK_IN",
                payload={
                    "booking_id": result.get("booking_id"),
                    "result": "VALID",
                    "customer_name": result.get("customer", {}).get("name") or "Player",
                    "turf_name": result.get("turf", {}).get("name") or "Pitch",
                    "scanned_at": timezone.now().strftime("%I:%M %p"),
                    "scanned_by": f"Override ({request.user.first_name or request.user.email})",
                },
            )

        return Response(result, status=status.HTTP_200_OK)


class GetBookingPassView(views.APIView):
    """
    Retrieves the authoritative digital match pass for a booking.
    """
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, booking_id):
        booking = get_object_or_404(Booking, booking_id=booking_id)

        # Allow access to the customer who owns it, or any staff/admin
        if request.user.role == "CUSTOMER" and booking.customer != request.user:
            return Response(
                {"error": "Unauthorized access to digital match pass."},
                status=status.HTTP_403_FORBIDDEN,
            )

        pass_payload = QRService.get_pass_payload(booking)
        return Response(pass_payload, status=status.HTTP_200_OK)


class CheckInLogsView(views.APIView):
    """
    Filterable check-in activity history with search and pagination.
    """
    permission_classes = [IsStaffOrAdmin]

    def get(self, request):
        date_filter = request.query_params.get("date", None)
        decision_filter = request.query_params.get("decision", None)
        search_query = request.query_params.get("search", "").strip()

        logs = CheckIn.objects.all().select_related("booking", "booking__customer", "booking__turf", "staff_user")

        if date_filter:
            logs = logs.filter(check_in_time__date=date_filter)

        if decision_filter:
            logs = logs.filter(decision=decision_filter)

        if search_query:
            logs = logs.filter(
                Q(booking__booking_id__icontains=search_query)
                | Q(booking__customer__first_name__icontains=search_query)
                | Q(booking__customer__last_name__icontains=search_query)
                | Q(staff_user__email__icontains=search_query)
                | Q(reason_code__icontains=search_query)
            )

        logs = logs.order_by("-check_in_time")[:100]

        data = [
            {
                "id": str(log.id),
                "booking_id": log.booking.booking_id,
                "customer_name": log.booking.customer.full_name or log.booking.customer.email,
                "customer_phone": getattr(log.booking.customer, "phone", "") or "—",
                "turf_name": log.booking.turf.name if log.booking.turf else "—",
                "staff_name": log.staff_user.get_full_name() or log.staff_user.email,
                "check_in_time": log.check_in_time.strftime("%d %b %Y, %I:%M %p"),
                "method": log.method,
                "decision": log.decision,
                "reason_code": log.reason_code,
                "message": log.message,
                "override_reason": log.override_reason,
            }
            for log in logs
        ]
        return Response(data, status=status.HTTP_200_OK)


class CheckInAnalyticsView(views.APIView):
    """
    Real-time gate admission telemetry & analytics for management and admins.
    """
    permission_classes = [IsStaffOrAdmin]

    def get(self, request):
        today = timezone.localdate()
        today_logs = CheckIn.objects.filter(check_in_time__date=today)

        total_scans = today_logs.count()
        total_approved = today_logs.filter(decision="ALLOW").count()
        total_denied = today_logs.filter(decision="DENY").count()
        total_overrides = today_logs.filter(method="ADMIN_OVERRIDE").count()
        duplicate_attempts = today_logs.filter(reason_code="ALREADY_CHECKED_IN").count()

        # Active passes today
        today_bookings = Booking.objects.filter(date=today, status__in=["CONFIRMED", "UPCOMING", "CHECKED_IN"])
        total_today_bookings = today_bookings.count()
        checked_in_bookings = today_bookings.filter(status="CHECKED_IN").count()
        pending_checkins = max(0, total_today_bookings - checked_in_bookings)

        # Anomaly flags
        anomalies = []
        if duplicate_attempts >= 3:
            anomalies.append({
                "severity": "HIGH",
                "title": "Multiple Duplicate Scan Attempts Detected",
                "detail": f"{duplicate_attempts} attempts to reuse already admitted match passes recorded today."
            })
        if total_overrides >= 5:
            anomalies.append({
                "severity": "MEDIUM",
                "title": "High Manual Override Frequency",
                "detail": f"{total_overrides} manual gate overrides executed today. Check gate operator logs."
            })

        return Response({
            "date": str(today),
            "today_scans": total_scans,
            "today_approved": total_approved,
            "today_denied": total_denied,
            "today_overrides": total_overrides,
            "duplicate_attempts": duplicate_attempts,
            "total_today_bookings": total_today_bookings,
            "checked_in_bookings": checked_in_bookings,
            "pending_checkins": pending_checkins,
            "approval_rate": round((total_approved / total_scans * 100), 1) if total_scans > 0 else 100.0,
            "anomalies": anomalies,
        }, status=status.HTTP_200_OK)


class AdminRevokePassView(views.APIView):
    """
    Administrative QR pass revocation endpoint.
    """
    permission_classes = [IsAdmin]

    def post(self, request):
        booking_id = request.data.get("booking_id", "").strip()
        reason = request.data.get("reason", "Administrative revocation").strip()

        booking = get_object_or_404(Booking, booking_id=booking_id)
        success = QRService.revoke_credential(booking, reason=reason, user=request.user)

        if not success:
            return Response(
                {"error": "No active credential found to revoke."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        return Response(
            {"message": f"Match pass for booking {booking_id} revoked successfully."},
            status=status.HTTP_200_OK,
        )


class AdminRegeneratePassView(views.APIView):
    """
    Administrative QR pass regeneration endpoint.
    """
    permission_classes = [IsAdmin]

    def post(self, request):
        booking_id = request.data.get("booking_id", "").strip()
        reason = request.data.get("reason", "Customer requested pass replacement").strip()

        booking = get_object_or_404(Booking, booking_id=booking_id)
        new_credential = QRService.regenerate_credential(
            booking=booking, reason=reason, user=request.user
        )

        return Response(
            {
                "message": f"New match pass generated for booking {booking_id}.",
                "credential_version": new_credential.credential_version,
                "ticket_code": new_credential.credential_token,
                "qr_base64": new_credential.qr_base64,
            },
            status=status.HTTP_200_OK,
        )
