from rest_framework import status, views, permissions
from rest_framework.response import Response
from .models import AuditLog
from accounts.permissions import IsAdmin


class AuditLogListView(views.APIView):
    permission_classes = [IsAdmin]

    def get(self, request):
        action_filter = request.query_params.get("action")
        logs = (
            AuditLog.objects.all().select_related("user").order_by("-created_at")[:100]
        )
        if action_filter:
            logs = logs.filter(action=action_filter.upper())

        data = [
            {
                "id": str(log.id),
                "user_email": log.user.email if log.user else "System",
                "action": log.action,
                "resource_type": log.resource_type,
                "resource_id": log.resource_id,
                "details": log.details,
                "ip_address": log.ip_address,
                "created_at": log.created_at.strftime("%Y-%m-%d %H:%M:%S"),
            }
            for log in logs
        ]

        return Response(data)
