from django.core.management.base import BaseCommand
from django.db import transaction
from django.db.models import Q

from accounts.models import User
from pricing.models import PricingRule
from promotions.models import Coupon
from bookings.models import Booking
from reviews.models import Review


class Command(BaseCommand):
    help = "Purges mock and synthetic seed data (demo bookings, mock pricing rules, demo users, mock reviews) safely."

    def add_arguments(self, parser):
        parser.add_argument(
            "--keep-users",
            action="store_true",
            help="Do not delete demo customer user accounts (customer@friendsturf.com, rahul@friendsturf.com)",
        )

    def handle(self, *args, **options):
        self.stdout.write(self.style.NOTICE("Starting purge of mock / synthetic seed data..."))

        keep_users = options.get("keep_users", False)

        with transaction.atomic():
            # 1. Identify mock bookings
            mock_user_emails = ["customer@friendsturf.com", "rahul@friendsturf.com"]

            mock_bookings_query = Booking.objects.filter(
                Q(customer__email__in=mock_user_emails)
                | Q(notes__icontains="Friends 7v7 friendly match")
                | Q(notes__icontains="Office cricket tournament practice")
            )
            mock_bookings = list(mock_bookings_query)
            booking_count = len(mock_bookings)

            # 2. Release any slots linked to these mock bookings
            released_slots_count = 0
            for b in mock_bookings:
                for slot in b.slots.all():
                    if slot.status in ("BOOKED", "LOCKED"):
                        slot.status = "AVAILABLE"
                        slot.locked_by = None
                        slot.locked_until = None
                        slot.booking_id = ""
                        slot.save(update_fields=["status", "locked_by", "locked_until", "booking_id"])
                        released_slots_count += 1

            # 3. Delete mock bookings (cascades payments, check-ins, QR credentials, reviews)
            if booking_count > 0:
                mock_bookings_query.delete()
                self.stdout.write(
                    self.style.SUCCESS(
                        f"Deleted {booking_count} mock bookings and released {released_slots_count} associated time slots."
                    )
                )
            else:
                self.stdout.write(self.style.NOTICE("No mock bookings found."))

            # 4. Delete mock reviews (if any remain)
            mock_reviews = Review.objects.filter(
                Q(review_text__icontains="Exceptional pitch quality")
                | Q(customer__email__in=mock_user_emails)
            )
            review_count = mock_reviews.count()
            if review_count > 0:
                mock_reviews.delete()
                self.stdout.write(self.style.SUCCESS(f"Deleted {review_count} mock reviews."))
            else:
                self.stdout.write(self.style.NOTICE("No mock reviews found."))

            # 5. Purge mock / synthetic pricing rules ("policies")
            seed_pricing_names = [
                "Weekend Prime Surge",
                "Weekday Afternoon Deal",
                "Night Floodlight Prime",
            ]
            mock_rules = PricingRule.objects.filter(name__in=seed_pricing_names)
            rules_count = mock_rules.count()
            if rules_count > 0:
                mock_rules.delete()
                self.stdout.write(
                    self.style.SUCCESS(
                        f"Deleted {rules_count} mock pricing rules ({', '.join(seed_pricing_names)})."
                    )
                )
            else:
                self.stdout.write(self.style.NOTICE("No mock pricing rules found."))

            # 6. Purge mock coupons
            seed_coupon_codes = ["WELCOME100", "TURF20", "FRIENDS10"]
            mock_coupons = Coupon.objects.filter(code__in=seed_coupon_codes)
            coupons_count = mock_coupons.count()
            if coupons_count > 0:
                mock_coupons.delete()
                self.stdout.write(
                    self.style.SUCCESS(
                        f"Deleted {coupons_count} mock coupons ({', '.join(seed_coupon_codes)})."
                    )
                )
            else:
                self.stdout.write(self.style.NOTICE("No mock coupons found."))

            # 7. Purge demo customer users (preserving real admins and staff)
            if not keep_users:
                mock_users = User.objects.filter(
                    email__in=mock_user_emails,
                    is_superuser=False,
                    is_staff=False,
                )
                users_count = mock_users.count()
                if users_count > 0:
                    mock_users.delete()
                    self.stdout.write(
                        self.style.SUCCESS(
                            f"Deleted {users_count} demo customer users ({', '.join(mock_user_emails)})."
                        )
                    )
                else:
                    self.stdout.write(self.style.NOTICE("No demo customer users found."))

        self.stdout.write(self.style.SUCCESS("Mock data purge completed successfully!"))
