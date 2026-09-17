from django.db import models
from django.conf import settings


class AuditLog(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="audit_logs",
    )
    action = models.CharField(max_length=100)
    resource_type = models.CharField(max_length=100)
    resource_id = models.CharField(max_length=255, blank=True)
    ip_address = models.CharField(max_length=100, blank=True)
    details = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        actor = self.user.email if self.user else "System/Anonymous"
        return f"[{self.created_at.strftime('%Y-%m-%d %H:%M')}] {actor} -> {self.action} on {self.resource_type}:{self.resource_id}"

    def save(self, *args, **kwargs):
        if self.action:
            self.action = str(self.action)[:100]
        if self.resource_type:
            self.resource_type = str(self.resource_type)[:100]
        if self.resource_id:
            self.resource_id = str(self.resource_id)[:255]
        if self.ip_address:
            self.ip_address = str(self.ip_address)[:100]
        super().save(*args, **kwargs)

    @classmethod
    def log(cls, user=None, action="", resource_type="AUTH", resource_id="", ip_address="", details=None, request=None):
        try:
            if request and not ip_address:
                x_forwarded_for = request.META.get("HTTP_X_FORWARDED_FOR")
                if x_forwarded_for:
                    ip_address = x_forwarded_for.split(",")[0].strip()
                else:
                    ip_address = request.META.get("REMOTE_ADDR", "")
            return cls.objects.create(
                user=user,
                action=str(action)[:100],
                resource_type=str(resource_type)[:100],
                resource_id=str(resource_id)[:255],
                ip_address=str(ip_address)[:100],
                details=details or {},
            )
        except Exception:
            return None
