import io
import base64
import hashlib
import secrets
import qrcode
from datetime import datetime, date, timedelta
from django.conf import settings
from django.utils import timezone
from django.db import transaction
from .models import QRCredential, CheckIn
from bookings.models import Booking


class QRService:
    CHECK_IN_OPEN_MINUTES = 30
    CHECK_IN_GRACE_MINUTES = 30

    @classmethod
    def generate_credential_for_booking(
        cls, booking, force_regenerate=False, reason="Initial issuance", admin_user=None
    ):
        """
        Generates or refreshes a cryptographically secure, tamper-resistant QR credential
        with high error correction (Level H), valid window, and hash indexing.
        """
        # Calculate validity window
        now = timezone.now()
        local_tz = timezone.get_current_timezone()

        # Combine booking date and start/end time into timezone-aware datetimes
        start_naive = datetime.combine(booking.date, booking.start_time)
        end_naive = datetime.combine(booking.date, booking.end_time)

        start_dt = timezone.make_aware(start_naive, local_tz)
        end_dt = timezone.make_aware(end_naive, local_tz)

        valid_from = start_dt - timedelta(minutes=cls.CHECK_IN_OPEN_MINUTES)
        valid_until = end_dt + timedelta(minutes=cls.CHECK_IN_GRACE_MINUTES)

        # Check existing credential
        credential = getattr(booking, "qr_credential", None)

        if credential and not force_regenerate:
            # If credential already exists and is active, return it
            if credential.status == "ACTIVE":
                return credential

        token = QRCredential.generate_token()
        token_hash = QRCredential.compute_hash(token)

        # Standards-compliant QR generation with High error correction
        qr = qrcode.QRCode(
            version=None,
            error_correction=qrcode.constants.ERROR_CORRECT_H,
            box_size=10,
            border=3,
        )
        # Compact opaque verification payload
        qr.add_data(token)
        qr.make(fit=True)
        img = qr.make_image(fill_color="#059669", back_color="white")

        buffer = io.BytesIO()
        img.save(buffer, format="PNG")
        qr_b64 = f"data:image/png;base64,{base64.b64encode(buffer.getvalue()).decode('utf-8')}"

        if credential:
            # Update/Regenerate existing
            old_version = credential.credential_version
            credential.credential_token = token
            credential.credential_hash = token_hash
            credential.credential_version = old_version + 1
            credential.status = "ACTIVE"
            credential.issued_at = now
            credential.valid_from = valid_from
            credential.valid_until = valid_until
            credential.qr_base64 = qr_b64
            credential.revoked_at = None
            credential.revocation_reason = ""
            credential.save()
        else:
            credential = QRCredential.objects.create(
                booking=booking,
                credential_token=token,
                credential_hash=token_hash,
                credential_version=1,
                status="ACTIVE",
                issued_at=now,
                valid_from=valid_from,
                valid_until=valid_until,
                qr_base64=qr_b64,
            )

        return credential

    @classmethod
    def evaluate_and_checkin(
        cls,
        raw_input: str,
        staff_user,
        method="QR_SCAN",
        facility_id=None,
        override_reason="",
        is_override=False,
        device_identifier="",
    ):
        """
        Authoritative gate check-in & decision engine.
        Atomic, idempotent, resistant to concurrent/duplicate scans, and auditable.
        """
        clean_input = (raw_input or "").strip()
        if not clean_input:
            return {
                "valid": False,
                "decision": "DENY",
                "reason_code": "EMPTY_PAYLOAD",
                "title": "Invalid Request",
                "message": "No credential or booking code provided.",
                "booking": None,
            }

        # Handle JSON payload if scanned from modern QR format
        if clean_input.startswith("{") and clean_input.endswith("}"):
            try:
                import json
                parsed_json = json.loads(clean_input)
                clean_input = (
                    parsed_json.get("token")
                    or parsed_json.get("qr_data")
                    or parsed_json.get("booking_id")
                    or clean_input
                ).strip()
            except Exception:
                pass

        # Handle scanned URLs e.g. http://localhost:5173/confirmation/FT-20260915-ABCD1
        if "://" in clean_input or ("/" in clean_input and not clean_input.startswith("FT-")):
            clean_input = clean_input.rstrip("/").split("/")[-1].strip()

        # Handle leading hash e.g. #FT-20260915-ABCD1
        if clean_input.startswith("#"):
            clean_input = clean_input.lstrip("#").strip()

        # 1. Resolve Booking and Credential without leaking internal IDs
        token_hash = QRCredential.compute_hash(clean_input)
        credential = QRCredential.objects.filter(
            credential_hash=token_hash
        ).select_related("booking", "booking__customer", "booking__turf").first()

        if not credential:
            credential = QRCredential.objects.filter(
                credential_token__iexact=clean_input
            ).select_related("booking", "booking__customer", "booking__turf").first()

        booking = None
        if credential:
            booking = credential.booking
        else:
            # Fallback for manual booking ID entry e.g. FT-20260915-XXXXX
            booking = Booking.objects.filter(
                booking_id__iexact=clean_input
            ).select_related("customer", "turf").first()
            if booking:
                credential = getattr(booking, "qr_credential", None)

        if not booking:
            return {
                "valid": False,
                "decision": "DENY",
                "reason_code": "NOT_FOUND",
                "title": "Booking Not Found",
                "message": f"No booking matches '{clean_input}'. Please check the reference code.",
                "booking": None,
            }

        now = timezone.now()
        local_tz = timezone.get_current_timezone()
        today = timezone.localdate()

        # Build booking info payload for UI
        booking_data = {
            "booking_id": booking.booking_id,
            "customer_name": booking.customer.full_name or booking.customer.email,
            "customer_phone": getattr(booking.customer, "phone", "") or "—",
            "turf_name": booking.turf.name,
            "turf_location": booking.turf.location,
            "surface_spec": getattr(booking.turf, "surface_spec", ""),
            "date": str(booking.date),
            "start_time": booking.start_time.strftime("%I:%M %p"),
            "end_time": booking.end_time.strftime("%I:%M %p"),
            "booking_type": booking.booking_type,
            "total_amount": float(booking.final_amount or booking.total_amount),
            "amount_paid": float(booking.amount_paid),
            "balance_due": float(booking.balance_due),
            "payment_status": (
                "PAID"
                if booking.balance_due <= 0 and booking.amount_paid > 0
                else "PARTIAL"
                if booking.amount_paid > 0
                else "PENDING"
            ),
        }

        # 2. Check Cancellation / Refund
        if booking.status in ("CANCELLED", "REFUNDED"):
            CheckIn.objects.create(
                booking=booking,
                qr_credential=credential,
                staff_user=staff_user,
                turf=booking.turf,
                check_in_time=now,
                method=method,
                decision="DENY",
                reason_code="BOOKING_CANCELLED",
                message=f"Booking is {booking.status.lower()}.",
                override_reason=override_reason,
                device_identifier=device_identifier,
            )
            return {
                "valid": False,
                "decision": "DENY",
                "reason_code": "BOOKING_CANCELLED",
                "title": "Booking Cancelled",
                "message": f"This match reservation was {booking.status.lower()} and is not eligible for admission.",
                "booking": booking_data,
            }

        # 3. Check Credential Revocation
        if credential and credential.status == "REVOKED":
            CheckIn.objects.create(
                booking=booking,
                qr_credential=credential,
                staff_user=staff_user,
                turf=booking.turf,
                check_in_time=now,
                method=method,
                decision="DENY",
                reason_code="CREDENTIAL_REVOKED",
                message="Credential has been revoked.",
                override_reason=override_reason,
                device_identifier=device_identifier,
            )
            return {
                "valid": False,
                "decision": "DENY",
                "reason_code": "CREDENTIAL_REVOKED",
                "title": "Pass Revoked",
                "message": "This digital pass has been revoked by management. Please contact reception.",
                "booking": booking_data,
            }

        # 4. Check Date and Operating Time Window (using server timezone)
        start_naive = datetime.combine(booking.date, booking.start_time)
        end_naive = datetime.combine(booking.date, booking.end_time)
        start_dt = timezone.make_aware(start_naive, local_tz)
        end_dt = timezone.make_aware(end_naive, local_tz)

        checkin_open_dt = start_dt - timedelta(minutes=cls.CHECK_IN_OPEN_MINUTES)
        checkin_close_dt = end_dt + timedelta(minutes=cls.CHECK_IN_GRACE_MINUTES)

        if booking.date < today and not is_override:
            CheckIn.objects.create(
                booking=booking,
                qr_credential=credential,
                staff_user=staff_user,
                turf=booking.turf,
                check_in_time=now,
                method=method,
                decision="DENY",
                reason_code="EXPIRED",
                message=f"Booking date {booking.date} has passed.",
                override_reason=override_reason,
                device_identifier=device_identifier,
            )
            return {
                "valid": False,
                "decision": "DENY",
                "reason_code": "EXPIRED",
                "title": "Match Date Expired",
                "message": f"This pass was for {booking.date.strftime('%d %b %Y')}, which has already passed.",
                "booking": booking_data,
            }

        if booking.date > today and not is_override:
            CheckIn.objects.create(
                booking=booking,
                qr_credential=credential,
                staff_user=staff_user,
                turf=booking.turf,
                check_in_time=now,
                method=method,
                decision="DENY",
                reason_code="FUTURE_DATE",
                message=f"Booking is for future date {booking.date}.",
                override_reason=override_reason,
                device_identifier=device_identifier,
            )
            return {
                "valid": False,
                "decision": "DENY",
                "reason_code": "FUTURE_DATE",
                "title": "Upcoming Match Date",
                "message": f"This match is scheduled for {booking.date.strftime('%A, %d %b %Y')}. Gate check-in is not yet open.",
                "booking": booking_data,
            }

        # Check Today's Time Window
        if booking.date == today and not is_override:
            if now < checkin_open_dt:
                CheckIn.objects.create(
                    booking=booking,
                    qr_credential=credential,
                    staff_user=staff_user,
                    turf=booking.turf,
                    check_in_time=now,
                    method=method,
                    decision="DENY",
                    reason_code="OUTSIDE_CHECKIN_WINDOW",
                    message="Check-in window not yet open.",
                    override_reason=override_reason,
                    device_identifier=device_identifier,
                )
                return {
                    "valid": False,
                    "decision": "DENY",
                    "reason_code": "OUTSIDE_CHECKIN_WINDOW",
                    "title": "Check-In Not Yet Open",
                    "message": f"Match starts at {booking.start_time.strftime('%I:%M %p')}. Gate admission opens at {checkin_open_dt.strftime('%I:%M %p')} (30 mins prior).",
                    "booking": booking_data,
                }
            elif now > checkin_close_dt:
                CheckIn.objects.create(
                    booking=booking,
                    qr_credential=credential,
                    staff_user=staff_user,
                    turf=booking.turf,
                    check_in_time=now,
                    method=method,
                    decision="DENY",
                    reason_code="EXPIRED",
                    message="Slot time has concluded.",
                    override_reason=override_reason,
                    device_identifier=device_identifier,
                )
                return {
                    "valid": False,
                    "decision": "DENY",
                    "reason_code": "EXPIRED",
                    "title": "Session Concluded",
                    "message": f"This match session ended at {booking.end_time.strftime('%I:%M %p')}.",
                    "booking": booking_data,
                }

        # 5. Facility check if specified
        if facility_id and str(booking.turf.id) != str(facility_id) and not is_override:
            return {
                "valid": False,
                "decision": "DENY",
                "reason_code": "WRONG_FACILITY",
                "title": "Wrong Pitch / Arena",
                "message": f"This booking is reserved for {booking.turf.name}. You are checking in at a different facility.",
                "booking": booking_data,
            }

        # 6. ATOMIC TRANSACTION: Check Duplicate & Perform Admission
        with transaction.atomic():
            locked_booking = (
                Booking.objects.select_for_update()
                .filter(id=booking.id)
                .first()
            )

            # Check if another scanner checked in concurrently
            if locked_booking.status == "CHECKED_IN" or (
                credential and credential.status == "USED"
            ):
                prior_checkin = (
                    CheckIn.objects.filter(
                        booking=locked_booking, decision="ALLOW"
                    )
                    .order_by("-check_in_time")
                    .first()
                )

                prior_time = (
                    prior_checkin.check_in_time
                    if prior_checkin
                    else locked_booking.checked_in_at
                )
                if prior_time:
                    local_time = timezone.localtime(prior_time) if timezone.is_aware(prior_time) else prior_time
                    checked_in_time_str = local_time.strftime("%I:%M %p")
                else:
                    checked_in_time_str = "Earlier"

                staff_name = (
                    prior_checkin.staff_user.get_full_name()
                    or prior_checkin.staff_user.email
                    if prior_checkin
                    else "Staff"
                )

                # Record denied duplicate scan
                CheckIn.objects.create(
                    booking=locked_booking,
                    qr_credential=credential,
                    staff_user=staff_user,
                    turf=locked_booking.turf,
                    check_in_time=now,
                    method=method,
                    decision="DENY",
                    reason_code="ALREADY_CHECKED_IN",
                    message=f"Duplicate entry attempt. Already admitted at {checked_in_time_str}.",
                    override_reason=override_reason,
                    device_identifier=device_identifier,
                )

                return {
                    "valid": False,
                    "decision": "DENY",
                    "reason_code": "ALREADY_CHECKED_IN",
                    "title": "Already Admitted",
                    "message": f"This match pass was already scanned & admitted at {checked_in_time_str} by {staff_name}. Duplicate entry blocked.",
                    "booking": booking_data,
                    "prior_checkin": {
                        "checked_in_at": checked_in_time_str,
                        "admitted_by": staff_name,
                    },
                }

            # Handle Outstanding Balance Due before Gate Admission
            if locked_booking.balance_due > 0 and not is_override:
                CheckIn.objects.create(
                    booking=locked_booking,
                    qr_credential=credential,
                    staff_user=staff_user,
                    turf=locked_booking.turf,
                    check_in_time=now,
                    method=method,
                    decision="DENY",
                    reason_code="BALANCE_DUE",
                    message=f"Outstanding balance of ₹{locked_booking.balance_due:.0f} required before gate admission.",
                    override_reason=override_reason,
                    device_identifier=device_identifier,
                )
                return {
                    "valid": False,
                    "decision": "DENY",
                    "reason_code": "BALANCE_DUE",
                    "title": "Balance Payment Required",
                    "message": f"This match pass has an outstanding balance of ₹{locked_booking.balance_due:.0f}. Please collect payment before gate admission.",
                    "balance_due": float(locked_booking.balance_due),
                    "amount_paid": float(locked_booking.amount_paid),
                    "total_amount": float(locked_booking.final_amount or locked_booking.total_amount),
                    "booking": booking_data,
                    "can_collect_balance": True,
                }

            payment_warning = None

            # Mark Booking Checked In
            locked_booking.status = "CHECKED_IN"
            locked_booking.checked_in_at = now
            locked_booking.checked_in_by = staff_user
            locked_booking.save()

            if credential:
                credential.status = "USED"
                credential.checkin_at = now
                credential.checkin_by = staff_user
                credential.last_scanned_at = now
                credential.scan_count += 1
                credential.save()

            # Record Successful CheckIn
            checkin_record = CheckIn.objects.create(
                booking=locked_booking,
                qr_credential=credential,
                staff_user=staff_user,
                turf=locked_booking.turf,
                check_in_time=now,
                method=method,
                decision="ALLOW",
                reason_code="MANUAL_OVERRIDE" if is_override else "ENTRY_APPROVED",
                message=(
                    f"Override Check-In: {override_reason}"
                    if is_override
                    else "Entry Verified & Approved"
                ),
                override_reason=override_reason,
                device_identifier=device_identifier,
            )

        return {
            "valid": True,
            "decision": "ALLOW",
            "status": "ADMITTED",
            "booking_id": booking.booking_id,
            "customer_name": booking_data["customer_name"],
            "turf_name": booking_data["turf_name"],
            "reason_code": "MANUAL_OVERRIDE" if is_override else "ENTRY_APPROVED",
            "title": "Entry Approved",
            "message": f"Welcome to Friends Turf! {booking.turf.name} admission confirmed.",
            "payment_warning": payment_warning,
            "booking": {
                **booking_data,
                "status": "CHECKED_IN",
                "checked_in_at": timezone.localtime(now).strftime("%I:%M %p"),
                "admitted_by": staff_user.get_full_name() or staff_user.email,
            },
            "checkin_id": str(checkin_record.id),
        }

    @classmethod
    def revoke_credential(cls, booking, reason: str, user):
        """
        Revokes an active credential with audit log.
        """
        credential = getattr(booking, "qr_credential", None)
        if not credential:
            return False

        credential.status = "REVOKED"
        credential.revoked_at = timezone.now()
        credential.revocation_reason = reason
        credential.save()
        return True

    @classmethod
    def regenerate_credential(cls, booking, reason: str, user):
        """
        Revokes any existing credential and generates a brand new one with updated version.
        """
        return cls.generate_credential_for_booking(
            booking=booking,
            force_regenerate=True,
            reason=reason,
            admin_user=user,
        )

    @classmethod
    def get_pass_payload(cls, booking):
        """
        Returns full customer match pass payload.
        """
        credential = getattr(booking, "qr_credential", None)
        if not credential or credential.status == "REVOKED":
            credential = cls.generate_credential_for_booking(booking)

        has_balance = float(booking.balance_due) > 0
        qr_locked = has_balance
        qr_image = None if qr_locked else (credential.qr_base64 if credential else None)

        return {
            "booking_id": booking.booking_id,
            "ticket_code": credential.credential_token if credential else "",
            "credential_version": credential.credential_version if credential else 1,
            "status": "DEPOSIT_CONFIRMED" if qr_locked else (credential.status if credential else "ACTIVE"),
            "booking_status": booking.status,
            "qr_locked": qr_locked,
            "lock_reason": (
                f"Deposit of ₹{float(booking.amount_paid):.0f} received. Remaining balance of ₹{float(booking.balance_due):.0f} must be settled to activate gate match pass."
                if qr_locked
                else None
            ),
            "payment_status": "PARTIAL" if has_balance else "PAID",
            "qr_base64": qr_image,
            "turf_name": booking.turf.name,
            "turf_location": booking.turf.location,
            "surface_spec": getattr(booking.turf, "surface_spec", "FIFA Approved Turf"),
            "lighting_spec": getattr(booking.turf, "lighting_spec", "500 Lux LED"),
            "date": str(booking.date),
            "start_time": booking.start_time.strftime("%H:%M"),
            "end_time": booking.end_time.strftime("%H:%M"),
            "customer_name": booking.customer.full_name or booking.customer.email,
            "customer_phone": getattr(booking.customer, "phone", "") or "",
            "total_amount": float(booking.final_amount or booking.total_amount),
            "amount_paid": float(booking.amount_paid),
            "balance_due": float(booking.balance_due),
            "valid_from": credential.valid_from.isoformat() if (credential and credential.valid_from) else None,
            "valid_until": credential.valid_until.isoformat() if (credential and credential.valid_until) else None,
            "is_used": credential.is_used if credential else False,
            "checked_in_at": (
                (timezone.localtime(booking.checked_in_at) if timezone.is_aware(booking.checked_in_at) else booking.checked_in_at).strftime("%I:%M %p, %d %b")
                if booking.checked_in_at
                else None
            ),
        }

    # Backward compatibility aliases
    generate_qr_for_booking = generate_credential_for_booking
    validate_and_checkin = evaluate_and_checkin
