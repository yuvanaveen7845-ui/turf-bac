from django.core.management.base import BaseCommand
from django.utils import timezone
from datetime import datetime, timedelta, time
from decimal import Decimal

from accounts.models import User, CustomerProfile, StaffProfile
from turfs.models import Facility, Turf, TimeSlot
from pricing.models import PricingRule, Holiday
from promotions.models import Coupon, ReferralReward
from memberships.models import MembershipPlan, CustomerMembership
from bookings.models import Booking
from bookings.services import BookingEngine
from payments.models import Payment
from qr_system.services import QRService
from reviews.models import Review
from notifications.models import Notification
from wallet.models import WalletTransaction, LoyaltyTransaction


class Command(BaseCommand):
    help = "Seeds complete realistic demo data for Friends Turf"

    def handle(self, *args, **options):
        self.stdout.write(self.style.NOTICE("Seeding Friends Turf database..."))

        # 1. Facilities
        facilities_data = [
            {
                "name": "FIFA Artificial Turf",
                "icon": "shield-check",
                "description": "50mm dual-tone imported monofilament grass",
            },
            {
                "name": "Pro Floodlights",
                "icon": "sun",
                "description": "400W anti-glare sports LED floodlights",
            },
            {
                "name": "Player Changing Room",
                "icon": "home",
                "description": "Clean individual lockers and shower cubicles",
            },
            {
                "name": "Free Parking",
                "icon": "car",
                "description": "Secure two-wheeler and four-wheeler parking",
            },
            {
                "name": "RO Drinking Water",
                "icon": "droplets",
                "description": "Free chilled purified drinking water",
            },
            {
                "name": "Clean Washrooms",
                "icon": "sparkles",
                "description": "Regularly sanitized hygiene facilities",
            },
            {
                "name": "Cricket Bowling Net",
                "icon": "target",
                "description": "Practice nets with automatic bowling machine",
            },
            {
                "name": "Seating Dugout",
                "icon": "users",
                "description": "Shaded substitution bench with tactical boards",
            },
        ]
        facility_objs = {}
        for f in facilities_data:
            obj, _ = Facility.objects.get_or_create(name=f["name"], defaults=f)
            facility_objs[f["name"]] = obj

        # 2. Demo Users
        admin_user = User.objects.filter(email="admin@friendsturf.com").first()
        if not admin_user:
            admin_user = User.objects.create_superuser(
                email="admin@friendsturf.com",
                password="admin123",
                first_name="Vikram",
                last_name="Singhania",
                phone="+91 98765 43210",
            )
        self.stdout.write(
            self.style.SUCCESS("Admin created: admin@friendsturf.com / admin123")
        )

        staff_user = User.objects.filter(email="staff@friendsturf.com").first()
        if not staff_user:
            staff_user = User.objects.create_user(
                email="staff@friendsturf.com",
                password="staff123",
                first_name="Arjun",
                last_name="Nair",
                phone="+91 98765 11223",
                role="STAFF",
            )
            staff_prof, _ = StaffProfile.objects.get_or_create(user=staff_user)
            staff_prof.employee_id = "FT-EMP-01"
            staff_prof.department = "Ground Operations & Check-in"
            staff_prof.save()
        self.stdout.write(
            self.style.SUCCESS("Staff created: staff@friendsturf.com / staff123")
        )

        customer_user = User.objects.filter(email="customer@friendsturf.com").first()
        if not customer_user:
            customer_user = User.objects.create_user(
                email="customer@friendsturf.com",
                password="customer123",
                first_name="Prajeeth",
                last_name="Kumar",
                phone="+91 98765 88990",
                role="CUSTOMER",
            )
            cust_prof = customer_user.customer_profile
            cust_prof.wallet_balance = Decimal("1500.00")
            cust_prof.loyalty_points = 350
            cust_prof.membership_tier = "GOLD"
            cust_prof.save()
        self.stdout.write(
            self.style.SUCCESS(
                "Customer created: customer@friendsturf.com / customer123"
            )
        )

        rahul_user = User.objects.filter(email="rahul@friendsturf.com").first()
        if not rahul_user:
            rahul_user = User.objects.create_user(
                email="rahul@friendsturf.com",
                password="rahul123",
                first_name="Rahul",
                last_name="Dravid",
                phone="+91 98765 44556",
                role="CUSTOMER",
                referred_by=customer_user,
            )
            r_prof = rahul_user.customer_profile
            r_prof.wallet_balance = Decimal("500.00")
            r_prof.loyalty_points = 120
            r_prof.membership_tier = "REGULAR"
            r_prof.save()

        # 3. Turfs
        turfs_data = [
            {
                "name": "Pitch 1 — Champions Football Arena",
                "slug": "champions-arena",
                "sport_type": "FOOTBALL",
                "description": "Premier Football pitch at Friends Turf. Equipped with shock-absorbent 50mm turf, high-output anti-glare LED floodlights, and shaded team dugouts.",
                "location": "Friends Turf, Tiruppur",
                "address": "Near Sirupooluvapatti, Kamatchepuram, Tiruppur, Tamil Nadu 641603 (RTO Office Backside)",
                "base_price": Decimal("1400.00"),
                "capacity": 14,
                "surface_spec": "50mm Monofilament Synthetic Turf",
                "is_fifa_certified": True,
                "lighting_spec": "400 Lux Anti-Glare Stadium LED Floodlights",
                "dugout_spec": "14-Player Shaded Dugout",
                "dimensions": "110ft x 70ft (7v7 Standard Pitch)",
                "fast_fill_threshold": 4,
                "operating_hours_start": time(5, 0),
                "operating_hours_end": time(23, 59),
                "slot_duration_minutes": 60,
                "images": [
                    "https://images.unsplash.com/photo-1575361204480-aadea25e6e68?auto=format&fit=crop&w=1200&q=80",
                    "https://images.unsplash.com/photo-1529900748604-07564a03e7a6?auto=format&fit=crop&w=1200&q=80",
                ],
                "rating": Decimal("4.9"),
                "total_reviews": 38,
                "facilities": [
                    "Artificial Turf",
                    "Pro Floodlights",
                    "Player Changing Room",
                    "Free Parking",
                    "RO Drinking Water",
                    "Seating Dugout",
                ],
            },
            {
                "name": "Pitch 2 — Legends Box Cricket & Futsal",
                "slug": "legends-box-cricket",
                "sport_type": "CRICKET",
                "description": "Tournament-spec Box Cricket & Futsal pitch at Friends Turf. Features seamless rebound boundary nets, consistent pitch bounce, and dedicated team dugout.",
                "location": "Friends Turf, Tiruppur",
                "address": "Near Sirupooluvapatti, Kamatchepuram, Tiruppur, Tamil Nadu 641603 (RTO Office Backside)",
                "base_price": Decimal("1200.00"),
                "capacity": 16,
                "surface_spec": "40mm High-Density Multi-Sport Dual Turf",
                "is_fifa_certified": True,
                "lighting_spec": "350 Lux Uniform Overhead Sports Lights",
                "dugout_spec": "16-Player Team Bench with Kit Storage",
                "dimensions": "100ft x 60ft (Box Cricket & 5v5 Futsal)",
                "fast_fill_threshold": 3,
                "operating_hours_start": time(5, 0),
                "operating_hours_end": time(23, 59),
                "slot_duration_minutes": 60,
                "images": [
                    "https://images.unsplash.com/photo-1531415074968-036ba1b575da?auto=format&fit=crop&w=1200&q=80",
                    "https://images.unsplash.com/photo-1540747913346-19e32dc3e97e?auto=format&fit=crop&w=1200&q=80",
                ],
                "rating": Decimal("4.8"),
                "total_reviews": 26,
                "facilities": [
                    "Artificial Turf",
                    "Pro Floodlights",
                    "Cricket Net",
                    "Free Parking",
                    "Clean Washrooms",
                ],
            },
            {
                "name": "Pitch 3 — Strikers Multi-Sport Arena",
                "slug": "strikers-multi-sport",
                "sport_type": "MULTI_SPORT",
                "description": "All-weather sports arena at Friends Turf. Sheltered and floodlit, ideal for Football, Box Cricket, and multi-sport tournaments.",
                "location": "Friends Turf, Tiruppur",
                "address": "Near Sirupooluvapatti, Kamatchepuram, Tiruppur, Tamil Nadu 641603 (RTO Office Backside)",
                "base_price": Decimal("1600.00"),
                "capacity": 18,
                "surface_spec": "55mm Cushioned Shockpad Hybrid Turf",
                "is_fifa_certified": True,
                "lighting_spec": "500 Lux All-Weather Tournament Illumination",
                "dugout_spec": "18-Player Air-Cooled Lounge Dugout",
                "dimensions": "120ft x 80ft (Full Multi-Sport Arena)",
                "fast_fill_threshold": 5,
                "operating_hours_start": time(6, 0),
                "operating_hours_end": time(23, 0),
                "slot_duration_minutes": 60,
                "images": [
                    "https://images.unsplash.com/photo-1587280501635-68a0e82cd5ff?auto=format&fit=crop&w=1200&q=80",
                    "https://images.unsplash.com/photo-1518091043644-c1d4457512c6?auto=format&fit=crop&w=1200&q=80",
                ],
                "rating": Decimal("5.0"),
                "total_reviews": 19,
                "facilities": [
                    "FIFA Artificial Turf",
                    "Pro Floodlights",
                    "Player Changing Room",
                    "Free Parking",
                    "RO Drinking Water",
                    "Clean Washrooms",
                    "Seating Dugout",
                ],
            },
        ]

        turf_objs = []
        for t_data in turfs_data:
            fac_names = t_data.pop("facilities")
            turf, created = Turf.objects.get_or_create(
                slug=t_data["slug"], defaults=t_data
            )
            if not created:
                for key, val in t_data.items():
                    setattr(turf, key, val)
                turf.save()
            for fn in fac_names:
                if fn in facility_objs:
                    turf.facilities.add(facility_objs[fn])
            turf_objs.append(turf)
        self.stdout.write(
            self.style.SUCCESS(f"Created/Updated {len(turf_objs)} Turfs with facilities")
        )

        # 4. Membership Plans
        membership_data = [
            {
                "name": "Silver",
                "slug": "silver",
                "tier_level": 1,
                "description": "Ideal for casual weekend warriors looking for steady savings.",
                "discount_percentage": Decimal("5.00"),
                "priority_booking_days": 7,
                "loyalty_point_multiplier": Decimal("1.00"),
                "monthly_price": Decimal("499.00"),
                "annual_price": Decimal("4999.00"),
                "features": [
                    "5% discount on all bookings",
                    "7 days advance slot booking",
                    "Standard loyalty points",
                    "Free locker access",
                ],
                "badge_color": "#94A3B8",
            },
            {
                "name": "Gold",
                "slug": "gold",
                "tier_level": 2,
                "description": "Our most popular tier for active team organizers and weekly regulars.",
                "discount_percentage": Decimal("10.00"),
                "priority_booking_days": 14,
                "loyalty_point_multiplier": Decimal("1.50"),
                "monthly_price": Decimal("899.00"),
                "annual_price": Decimal("8999.00"),
                "features": [
                    "10% discount on all bookings",
                    "14 days priority slot booking",
                    "1.5x loyalty points earning",
                    "Free ball and bib rental",
                    "Zero cancellation charges up to 12h",
                ],
                "badge_color": "#F59E0B",
            },
            {
                "name": "Platinum",
                "slug": "platinum",
                "tier_level": 3,
                "description": "Elite club access with maximum savings, prime hour priority, and VIP concierge.",
                "discount_percentage": Decimal("15.00"),
                "priority_booking_days": 30,
                "loyalty_point_multiplier": Decimal("2.00"),
                "monthly_price": Decimal("1499.00"),
                "annual_price": Decimal("14999.00"),
                "features": [
                    "15% discount on all bookings",
                    "30 days VIP advance booking",
                    "2x loyalty points on every game",
                    "Priority tournament invitations",
                    "Complimentary energy drinks each visit",
                ],
                "badge_color": "#8B5CF6",
            },
        ]
        for m in membership_data:
            MembershipPlan.objects.get_or_create(slug=m["slug"], defaults=m)

        # 5. Pricing Rules
        pricing_rules_data = [
            {
                "name": "Weekend Prime Surge",
                "rule_type": "WEEKEND",
                "adjustment_type": "PERCENTAGE",
                "adjustment_value": Decimal("20.00"),
                "applicable_days": [5, 6],  # Sat, Sun
                "priority": 15,
                "is_active": True,
            },
            {
                "name": "Weekday Afternoon Deal",
                "rule_type": "OFF_PEAK",
                "adjustment_type": "PERCENTAGE",
                "adjustment_value": Decimal("-15.00"),
                "applicable_days": [0, 1, 2, 3, 4],  # Mon-Fri
                "start_time": time(11, 0),
                "end_time": time(16, 0),
                "priority": 12,
                "is_active": True,
            },
            {
                "name": "Night Floodlight Prime",
                "rule_type": "PEAK_HOUR",
                "adjustment_type": "PERCENTAGE",
                "adjustment_value": Decimal("15.00"),
                "start_time": time(19, 0),
                "end_time": time(23, 0),
                "priority": 10,
                "is_active": True,
            },
        ]
        for pr in pricing_rules_data:
            PricingRule.objects.get_or_create(name=pr["name"], defaults=pr)

        # 6. Coupons
        coupons_data = [
            {
                "code": "WELCOME100",
                "title": "New Player Kickoff",
                "description": "Flat ₹100 discount on your first turf booking",
                "discount_type": "FIXED",
                "discount_value": Decimal("100.00"),
                "min_booking_amount": Decimal("500.00"),
                "start_date": timezone.now().date() - timedelta(days=10),
                "end_date": timezone.now().date() + timedelta(days=90),
                "usage_limit": 500,
                "per_user_limit": 1,
                "coupon_type": "FIRST_BOOKING",
            },
            {
                "code": "TURF20",
                "title": "Grand Weekend 20% Off",
                "description": "20% discount on all bookings above ₹1000",
                "discount_type": "PERCENTAGE",
                "discount_value": Decimal("20.00"),
                "min_booking_amount": Decimal("1000.00"),
                "max_discount_amount": Decimal("400.00"),
                "start_date": timezone.now().date() - timedelta(days=5),
                "end_date": timezone.now().date() + timedelta(days=60),
                "usage_limit": 200,
                "per_user_limit": 2,
                "coupon_type": "WEEKEND",
            },
            {
                "code": "FRIENDS10",
                "title": "Squad Saver",
                "description": "10% discount on regular team matches",
                "discount_type": "PERCENTAGE",
                "discount_value": Decimal("10.00"),
                "min_booking_amount": Decimal("800.00"),
                "start_date": timezone.now().date() - timedelta(days=5),
                "end_date": timezone.now().date() + timedelta(days=120),
                "usage_limit": 1000,
                "per_user_limit": 5,
                "coupon_type": "GENERAL",
            },
        ]
        for cp in coupons_data:
            Coupon.objects.get_or_create(code=cp["code"], defaults=cp)

        # 7. Generate Slots for Today and Next 7 Days
        today = timezone.now().date()
        for offset in range(8):
            day = today + timedelta(days=offset)
            for turf in turf_objs:
                cur_t = turf.operating_hours_start
                while cur_t < turf.operating_hours_end:
                    start_dt = datetime.combine(day, cur_t)
                    end_dt = start_dt + timedelta(minutes=turf.slot_duration_minutes)
                    end_t = end_dt.time()
                    if end_t > turf.operating_hours_end:
                        break
                    TimeSlot.objects.get_or_create(
                        turf=turf,
                        date=day,
                        start_time=cur_t,
                        end_time=end_t,
                        defaults={"status": "AVAILABLE", "price": turf.base_price},
                    )
                    cur_t = end_t

        self.stdout.write(self.style.SUCCESS("Generated slots for next 8 days"))

        # 8. Create Realistic Demo Bookings
        # Booking 1: Upcoming Confirmed Booking for today evening with QR code
        slot_today_1 = TimeSlot.objects.filter(
            turf=turf_objs[0], date=today, start_time=time(19, 0)
        ).first()
        if slot_today_1 and slot_today_1.status != "BOOKED":
            b1 = BookingEngine.create_booking(
                turf=turf_objs[0],
                date_obj=today,
                slot_ids=[str(slot_today_1.id)],
                user=customer_user,
                payment_type="FULL",
                payment_method="UPI",
                notes="Friends 7v7 friendly match. Need size 5 football.",
            )
            self.stdout.write(
                self.style.SUCCESS(
                    f"Created Booking 1: {b1.booking_id} (CONFIRMED for Today 19:00)"
                )
            )

        # Booking 2: Checked-In Booking for Today (In Progress)
        slot_today_2 = TimeSlot.objects.filter(
            turf=turf_objs[1], date=today, start_time=time(17, 0)
        ).first()
        if slot_today_2 and slot_today_2.status != "BOOKED":
            b2 = BookingEngine.create_booking(
                turf=turf_objs[1],
                date_obj=today,
                slot_ids=[str(slot_today_2.id)],
                user=customer_user,
                payment_type="FULL",
                payment_method="CARD",
                notes="Office cricket tournament practice",
            )
            # Mark checked in
            QRService.validate_and_checkin(
                b2.booking_id, staff_user, "VIP Customer, fast pass"
            )
            self.stdout.write(
                self.style.SUCCESS(f"Created Booking 2: {b2.booking_id} (CHECKED_IN)")
            )

        # Booking 3: Completed Booking from 2 days ago with Review
        past_date = today - timedelta(days=2)
        slot_past = TimeSlot.objects.filter(turf=turf_objs[0], date=past_date).first()
        if not slot_past:
            slot_past = TimeSlot.objects.create(
                turf=turf_objs[0],
                date=past_date,
                start_time=time(18, 0),
                end_time=time(19, 0),
                status="BOOKED",
                price=turf_objs[0].base_price,
            )
        b3 = Booking.objects.create(
            booking_id=Booking.generate_booking_id(past_date),
            customer=customer_user,
            turf=turf_objs[0],
            date=past_date,
            start_time=time(18, 0),
            end_time=time(19, 0),
            status="COMPLETED",
            total_amount=Decimal("1400.00"),
            discount_amount=Decimal("140.00"),
            tax_amount=Decimal("226.80"),
            final_amount=Decimal("1486.80"),
            amount_paid=Decimal("1486.80"),
            balance_due=Decimal("0.00"),
            checked_in_at=datetime.combine(past_date, time(17, 55)),
            completed_at=datetime.combine(past_date, time(19, 5)),
        )
        b3.slots.add(slot_past)
        QRService.generate_qr_for_booking(b3)

        # Add Review for Booking 3
        Review.objects.get_or_create(
            booking=b3,
            defaults={
                "customer": customer_user,
                "turf": turf_objs[0],
                "rating": 5,
                "facility_rating": 5,
                "staff_rating": 5,
                "review_text": "Exceptional pitch quality! The floodlights are bright and non-glaring. The staff welcomed our squad warmly.",
                "suggestions": "Add chilled Gatorade in the dugout fridge!",
                "admin_response": "Thank you Prajeeth! We have now stocked sports drinks at the counter.",
            },
        )
        self.stdout.write(
            self.style.SUCCESS(
                f"Created Booking 3: {b3.booking_id} (COMPLETED with 5-star Review)"
            )
        )

        self.stdout.write(
            self.style.SUCCESS("All Friends Turf demo data successfully seeded!")
        )
