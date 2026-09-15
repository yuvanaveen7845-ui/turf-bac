from rest_framework import serializers
from .models import Maintenance
from turfs.serializers import TurfSerializer


class MaintenanceSerializer(serializers.ModelSerializer):
    turf_name = serializers.ReadOnlyField(source="turf.name")
    staff_name = serializers.ReadOnlyField(source="assigned_staff.full_name")

    class Meta:
        model = Maintenance
        fields = [
            "id",
            "turf",
            "turf_name",
            "date",
            "start_time",
            "end_time",
            "reason",
            "assigned_staff",
            "staff_name",
            "status",
            "notes",
            "created_at",
        ]
