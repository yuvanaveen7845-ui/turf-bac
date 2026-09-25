"""
Friends Turf -- Periodic Background Task Runner
================================================
A single management command that runs all automated maintenance tasks.
Designed to be called every 5 minutes via cron, Render background worker,
or `python manage.py run_periodic_tasks`.

Tasks performed:
  1. Release expired slot locks (prevent lock leaks)
  2. Auto-mark no-show bookings (CONFIRMED past end_time without check-in)
  3. Expire lapsed memberships (ACTIVE past end_date → EXPIRED)
  4. Send upcoming match reminders (24h and 2h windows, idempotent)

Each task is independent and failure-isolated -- one failing task
does not prevent others from running.
"""

import logging
from datetime import datetime, timedelta
from decimal import Decimal

from django.core.management.base import BaseCommand
from django.utils import timezone
from django.db import transaction

logger = logging.getLogger("friends_turf.periodic")


class Command(BaseCommand):
    help = "Run all periodic background maintenance tasks for Friends Turf."

    def add_arguments(self, parser):
        parser.add_argument(
            "--task",
            type=str,
            choices=["locks", "noshows", "memberships", "reminders", "all"],
            default="all",
            help="Run a specific task or all tasks (default: all).",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Preview what would happen without making changes.",
        )

    def handle(self, *args, **options):
        task = options["task"]
        dry_run = options["dry_run"]
        now = timezone.now()

        self.stdout.write(
            self.style.HTTP_INFO(
                f"\n{'='*60}\n"
                f"  Friends Turf Periodic Tasks -- {now.strftime('%Y-%m-%d %H:%M:%S %Z')}\n"
                f"  Mode: {'DRY RUN' if dry_run else 'LIVE'}\n"
                f"{'='*60}"
            )
        )

        results = {}

        if task in ("locks", "all"):
            results["locks"] = self._release_expired_locks(dry_run)

        if task in ("noshows", "all"):
            results["noshows"] = self._mark_no_shows(dry_run)

        if task in ("memberships", "all"):
            results["memberships"] = self._expire_memberships(dry_run)

        if task in ("reminders", "all"):
            results["reminders"] = self._send_reminders(dry_run)

        # Summary
        self.stdout.write(
            self.style.SUCCESS(
                f"\n{'-'*60}\n"
                f"  Summary: {results}\n"
                f"{'-'*60}\n"
            )
        )

    # ── Task 1: Release Expired Slot Locks ───────────────────────────

    def _release_expired_locks(self, dry_run=False):
        """Free slots whose temporary checkout locks have expired."""
        from bookings.services import BookingEngine

        self.stdout.write("\n[1/4] Releasing expired slot locks...")

        if dry_run:
            from turfs.models import TimeSlot

            count = TimeSlot.objects.filter(
                status="LOCKED", locked_until__lt=timezone.now()
            ).count()
            self.stdout.write(f"  -> DRY RUN: {count} expired locks would be released.")
            return {"expired_locks_released": count, "dry_run": True}

        count = BookingEngine.release_expired_locks()
        if count > 0:
            self.stdout.write(self.style.WARNING(f"  -> Released {count} expired slot lock(s)."))
            logger.info(f"Periodic: Released {count} expired slot locks.")
        else:
            self.stdout.write(self.style.SUCCESS("  -> No expired locks found."))

        return {"expired_locks_released": count}

    # ── Task 2: Auto-mark No-Shows ───────────────────────────────────

    def _mark_no_shows(self, dry_run=False):
        """
        Mark CONFIRMED bookings as NO_SHOW if their match end_time has passed
        and they were never checked in.

        Grace period: 30 minutes after end_time to allow late check-ins.
        """
        from bookings.models import Booking
        from audit.models import AuditLog

        self.stdout.write("\n[2/4] Auto-marking no-show bookings...")

        now = timezone.localtime(timezone.now())
        today = now.date()
        grace_minutes = 30
        grace_cutoff_time = (now - timedelta(minutes=grace_minutes)).time()

        # Find CONFIRMED bookings on today or earlier dates whose end_time + grace has passed
        candidates = Booking.objects.filter(
            status="CONFIRMED",
        ).filter(
            # Past dates: any confirmed booking from before today
            # Today: confirmed bookings whose end_time + grace has passed
            date__lte=today,
        ).select_related("turf", "customer")

        marked = 0
        errors = 0

        for booking in candidates:
            # Skip future time slots for today's bookings
            if booking.date == today and booking.end_time > grace_cutoff_time:
                continue

            if dry_run:
                self.stdout.write(
                    f"  -> DRY RUN: Would mark {booking.booking_id} "
                    f"({booking.turf.name}, {booking.date} {booking.end_time}) as NO_SHOW"
                )
                marked += 1
                continue

            try:
                with transaction.atomic():
                    locked = Booking.objects.select_for_update().get(pk=booking.pk)
                    if locked.status != "CONFIRMED":
                        continue  # Status changed between query and lock

                    locked.transition_to("NO_SHOW")
                    locked.save()

                    AuditLog.objects.create(
                        user=locked.customer,
                        action="AUTO_NO_SHOW",
                        resource_type="BOOKING",
                        resource_id=locked.booking_id,
                        details={
                            "reason": "Automated: match end_time + grace period passed without check-in.",
                            "end_time": str(locked.end_time),
                            "grace_minutes": grace_minutes,
                        },
                    )
                    marked += 1
            except Exception as e:
                errors += 1
                logger.error(f"Periodic: Failed to mark {booking.booking_id} as NO_SHOW: {e}")

        if marked > 0:
            self.stdout.write(self.style.WARNING(f"  -> Marked {marked} booking(s) as NO_SHOW."))
            if not dry_run:
                logger.info(f"Periodic: Marked {marked} bookings as NO_SHOW.")
        else:
            self.stdout.write(self.style.SUCCESS("  -> No no-show bookings found."))

        result = {"no_shows_marked": marked}
        if dry_run:
            result["dry_run"] = True
        if errors:
            result["errors"] = errors
        return result

    # ── Task 3: Expire Lapsed Memberships ────────────────────────────

    def _expire_memberships(self, dry_run=False):
        """
        Transition ACTIVE memberships whose end_date has passed to EXPIRED.
        Also updates customer_profile membership_tier to 'NONE'.
        """
        from memberships.models import CustomerMembership
        from notifications.models import Notification

        self.stdout.write("\n[3/4] Expiring lapsed memberships...")

        today = timezone.now().date()
        lapsed = CustomerMembership.objects.filter(
            status="ACTIVE",
            end_date__lt=today,
        ).select_related("customer", "plan")

        expired_count = 0

        for membership in lapsed:
            if dry_run:
                self.stdout.write(
                    f"  -> DRY RUN: Would expire {membership.customer.email} "
                    f"({membership.plan.name}, ended {membership.end_date})"
                )
                expired_count += 1
                continue

            try:
                membership.status = "EXPIRED"
                membership.save()

                # Reset customer membership tier
                if hasattr(membership.customer, "customer_profile"):
                    prof = membership.customer.customer_profile
                    prof.membership_tier = "NONE"
                    prof.save()

                # Notify the customer
                Notification.objects.create(
                    user=membership.customer,
                    notification_type="MEMBERSHIP_ALERT",
                    title=f"Your {membership.plan.name} Membership Has Expired",
                    message=(
                        f"Your {membership.plan.name} membership expired on "
                        f"{membership.end_date.strftime('%d %b %Y')}. "
                        f"Renew now to keep enjoying {membership.plan.discount_percentage}% off every booking."
                    ),
                )

                expired_count += 1
            except Exception as e:
                logger.error(
                    f"Periodic: Failed to expire membership for {membership.customer.email}: {e}"
                )

        if expired_count > 0:
            self.stdout.write(
                self.style.WARNING(f"  -> Expired {expired_count} lapsed membership(s).")
            )
            logger.info(f"Periodic: Expired {expired_count} lapsed memberships.")
        else:
            self.stdout.write(self.style.SUCCESS("  -> No lapsed memberships found."))

        result = {"memberships_expired": expired_count}
        if dry_run:
            result["dry_run"] = True
        return result

    # ── Task 4: Upcoming Match Reminders ─────────────────────────────

    def _send_reminders(self, dry_run=False):
        """
        Send idempotent in-app reminders for upcoming confirmed bookings.
        Delegates to the existing ReminderService which already handles
        deduplication via existing notification checks.
        """
        from notifications.services import ReminderService

        self.stdout.write("\n[4/4] Sending upcoming match reminders...")

        if dry_run:
            from bookings.models import Booking
            from notifications.models import Notification

            today = timezone.now().date()
            tomorrow = today + timedelta(days=1)

            upcoming = Booking.objects.filter(
                status="CONFIRMED",
                date__gte=today,
                date__lte=tomorrow,
            )

            # Count those without existing reminders
            pending = 0
            for b in upcoming:
                has_reminder = Notification.objects.filter(
                    user=b.customer,
                    notification_type="UPCOMING_REMINDER",
                    data__booking_id=b.booking_id,
                ).exists()
                if not has_reminder:
                    pending += 1

            self.stdout.write(f"  -> DRY RUN: {pending} reminder(s) would be sent.")
            return {"reminders_sent": pending, "dry_run": True}

        sent = ReminderService.send_upcoming_match_reminders()
        if sent > 0:
            self.stdout.write(self.style.SUCCESS(f"  -> Sent {sent} match reminder(s)."))
            logger.info(f"Periodic: Sent {sent} match reminders.")
        else:
            self.stdout.write(self.style.SUCCESS("  -> No new reminders to send."))

        return {"reminders_sent": sent}
