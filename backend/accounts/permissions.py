import logging
from rest_framework import permissions

logger = logging.getLogger("accounts.permissions")

# Enterprise Action Permissions Catalog
CUSTOMER_PERMISSIONS = {
    "BOOKING_VIEW_OWN",
    "BOOKING_CREATE",
    "BOOKING_CANCEL_OWN",
    "BOOKING_RESCHEDULE_OWN",
    "PAYMENT_VIEW_OWN",
    "FACILITY_VIEW",
    "PROFILE_MANAGE",
    # Aliases for backwards compatibility
    "browse_facilities",
    "create_booking",
    "view_own_bookings",
    "cancel_own_booking",
    "reschedule_own_booking",
    "view_own_invoices",
    "view_own_profile",
}

STAFF_PERMISSIONS = {
    *CUSTOMER_PERMISSIONS,
    "BOOKING_VIEW",
    "BOOKING_EDIT",
    "CHECKIN_VIEW",
    "CHECKIN_SCAN",
    "CHECKIN_MANUAL",
    "CUSTOMER_VIEW",
    "PAYMENT_RECORD_OFFLINE",
    "PAYMENT_VIEW",
    # Aliases
    "manage_bookings",
    "view_customers",
    "manage_walkins",
    "view_schedule",
    "scan_qr",
}

ADMIN_PERMISSIONS = {
    *STAFF_PERMISSIONS,
    "BOOKING_CANCEL",
    "BOOKING_RESCHEDULE",
    "PAYMENT_REFUND",
    "PAYMENT_ADJUST",
    "PRICING_VIEW",
    "PRICING_EDIT",
    "PRICING_OVERRIDE",
    "FACILITY_CREATE",
    "FACILITY_EDIT",
    "FACILITY_BLOCK",
    "FACILITY_DELETE",
    "CUSTOMER_EDIT",
    "CUSTOMER_NOTES",
    "REPORT_VIEW",
    "REPORT_EXPORT",
    "STAFF_VIEW",
    "STAFF_CREATE",
    "STAFF_EDIT",
    "STAFF_SUSPEND",
    "CHECKIN_OVERRIDE",
    "AUDIT_VIEW",
    "SETTINGS_VIEW",
    "SETTINGS_EDIT",
    "FEATURES_MANAGE",
    # Aliases
    "manage_facilities",
    "manage_customers",
    "manage_payments",
    "process_refunds",
    "view_reports",
    "manage_pricing",
    "manage_coupons",
    "manage_maintenance",
    "view_audit_logs",
    "manage_users",
    "manage_staff",
    "manage_settings",
}

ROLE_PERMISSIONS = {
    "ADMIN": ADMIN_PERMISSIONS,
    "STAFF": STAFF_PERMISSIONS,
    "CUSTOMER": CUSTOMER_PERMISSIONS,
}


def get_user_permissions(user):
    """
    Returns the set of active permissions for the given user based on their role and status.
    Inactive, suspended, or disabled users receive an empty set.
    """
    if not user or not user.is_authenticated:
        return set()
    if not user.is_active or getattr(user, "status", None) != "ACTIVE":
        return set()
    if user.is_superuser:
        return ADMIN_PERMISSIONS.copy()
    return ROLE_PERMISSIONS.get(user.role, set()).copy()


def user_has_permission(user, permission_name):
    """
    Verifies if an authenticated, active user possesses the requested permission.
    """
    if not user or not user.is_authenticated:
        return False
    if not user.is_active or getattr(user, "status", None) != "ACTIVE":
        return False
    if user.is_superuser:
        return True
    perms = get_user_permissions(user)
    return permission_name in perms


class HasPermission(permissions.BasePermission):
    """
    Checks if the user has a specific permission defined by `required_permission` on the view.
    """
    def has_permission(self, request, view):
        perm = getattr(view, "required_permission", None)
        if not perm:
            return True
        return user_has_permission(request.user, perm)


class IsCustomer(permissions.BasePermission):
    def has_permission(self, request, view):
        return bool(
            request.user
            and request.user.is_authenticated
            and request.user.role == "CUSTOMER"
            and request.user.status == "ACTIVE"
            and request.user.is_active
        )


class IsB2BUser(permissions.BasePermission):
    """Allows access to active B2B users (STAFF, ADMIN)."""
    def has_permission(self, request, view):
        return bool(
            request.user
            and request.user.is_authenticated
            and request.user.role in ("STAFF", "ADMIN")
            and request.user.status == "ACTIVE"
            and request.user.is_active
        )


class IsStaff(permissions.BasePermission):
    def has_permission(self, request, view):
        return bool(
            request.user
            and request.user.is_authenticated
            and request.user.role in ("STAFF", "ADMIN")
            and request.user.status == "ACTIVE"
            and request.user.is_active
        )


class IsManager(permissions.BasePermission):
    """Legacy alias for backward compatibility — checks for Admin privileges."""
    def has_permission(self, request, view):
        return bool(
            request.user
            and request.user.is_authenticated
            and (request.user.role == "ADMIN" or request.user.is_superuser)
            and request.user.status == "ACTIVE"
            and request.user.is_active
        )


class IsAdmin(permissions.BasePermission):
    def has_permission(self, request, view):
        return bool(
            request.user
            and request.user.is_authenticated
            and (request.user.role == "ADMIN" or request.user.is_superuser)
            and request.user.status == "ACTIVE"
            and request.user.is_active
        )


class IsStaffOrAdmin(permissions.BasePermission):
    def has_permission(self, request, view):
        return bool(
            request.user
            and request.user.is_authenticated
            and (
                request.user.role in ("STAFF", "ADMIN")
                or request.user.is_superuser
            )
            and request.user.status == "ACTIVE"
            and request.user.is_active
        )


class IsOwnerOrBusinessUser(permissions.BasePermission):
    """
    Object-level permission allowing Customers to access only their own records,
    while authorized Business users (STAFF, ADMIN) can manage them.
    """
    def has_object_permission(self, request, view, obj):
        if not request.user or not request.user.is_authenticated:
            return False
        if request.user.status != "ACTIVE" or not request.user.is_active:
            return False
        if request.user.role in ("STAFF", "ADMIN") or request.user.is_superuser:
            return True

        # Check customer ownership
        if hasattr(obj, "customer"):
            return obj.customer == request.user
        if hasattr(obj, "user"):
            return obj.user == request.user
        return False


class CanProcessRefunds(permissions.BasePermission):
    """
    Enforces that only Admins (or accounts with PAYMENT_REFUND permission)
    can initiate refunds. General staff are restricted.
    """
    def has_permission(self, request, view):
        return bool(
            request.user
            and request.user.is_authenticated
            and request.user.status == "ACTIVE"
            and request.user.is_active
            and (
                request.user.role == "ADMIN"
                or request.user.is_superuser
                or user_has_permission(request.user, "PAYMENT_REFUND")
                or user_has_permission(request.user, "process_refunds")
            )
        )

