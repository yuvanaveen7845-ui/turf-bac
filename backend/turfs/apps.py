import logging
from datetime import time, timedelta
from decimal import Decimal
from django.apps import AppConfig
from django.db.models.signals import post_migrate

logger = logging.getLogger(__name__)


def ensure_default_turfs(sender, **kwargs):
    if sender.name != "turfs":
        return
    try:
        from django.utils import timezone
        from .models import Turf, Facility
        from .services import SchedulingEngine

        active_turfs = Turf.objects.filter(is_active=True, is_deleted=False).count()
        if active_turfs == 0:
            logger.info("No active turfs found. Auto-provisioning primary arenas...")

            fac_names = [
                "Artificial Turf",
                "Pro Floodlights",
                "Player Changing Room",
                "Free Parking",
                "RO Drinking Water",
                "Seating Dugout",
            ]
            fac_objs = []
            for name in fac_names:
                f_obj, _ = Facility.objects.get_or_create(
                    name=name,
                    defaults={"icon": "shield-check", "description": f"Standard {name}"},
                )
                fac_objs.append(f_obj)

            t1 = Turf.objects.filter(slug="champions-arena").first()
            if not t1:
                t1 = Turf.objects.create(
                    name="Pitch 1 — Champions Football Arena",
                    slug="champions-arena",
                    sport_type="FOOTBALL",
                    description="Premier Football pitch at Friends Turf. Equipped with shock-absorbent 50mm turf, high-output anti-glare LED floodlights, and shaded team dugouts.",
                    location="Friends Turf, Tiruppur",
                    address="Near Sirupooluvapatti, Kamatchepuram, Tiruppur, Tamil Nadu 641603 (RTO Office Backside)",
                    base_price=Decimal("1000.00"),
                    capacity=14,
                    surface_spec="50mm Monofilament Synthetic Turf",
                    is_fifa_certified=True,
                    lighting_spec="400 Lux Anti-Glare Stadium LED Floodlights",
                    dugout_spec="14-Player Shaded Dugout",
                    dimensions="110ft x 70ft (7v7 Standard Pitch)",
                    fast_fill_threshold=4,
                    operating_hours_start=time(5, 0),
                    operating_hours_end=time(23, 59),
                    slot_duration_minutes=30,
                    is_active=True,
                    is_deleted=False,
                    images=[
                        "https://images.unsplash.com/photo-1575361204480-aadea25e6e68?auto=format&fit=crop&w=1200&q=80",
                        "https://images.unsplash.com/photo-1529900748604-07564a03e7a6?auto=format&fit=crop&w=1200&q=80",
                    ],
                )
                t1.facilities.set(fac_objs)
            else:
                t1.is_active = True
                t1.is_deleted = False
                t1.save(update_fields=["is_active", "is_deleted"])

            t2 = Turf.objects.filter(slug="legends-box-cricket").first()
            if not t2:
                t2 = Turf.objects.create(
                    name="Pitch 2 — Legends Box Cricket & Futsal",
                    slug="legends-box-cricket",
                    sport_type="CRICKET",
                    description="Tournament-spec Box Cricket & Futsal pitch at Friends Turf. Features seamless rebound boundary nets, consistent pitch bounce, and dedicated team dugout.",
                    location="Friends Turf, Tiruppur",
                    address="Near Sirupooluvapatti, Kamatchepuram, Tiruppur, Tamil Nadu 641603 (RTO Office Backside)",
                    base_price=Decimal("1200.00"),
                    capacity=16,
                    surface_spec="40mm High-Density Multi-Sport Dual Turf",
                    is_fifa_certified=True,
                    lighting_spec="350 Lux Uniform Overhead Sports Lights",
                    dugout_spec="16-Player Team Bench with Kit Storage",
                    dimensions="100ft x 60ft (Box Cricket & 5v5 Futsal)",
                    fast_fill_threshold=3,
                    operating_hours_start=time(5, 0),
                    operating_hours_end=time(23, 59),
                    slot_duration_minutes=60,
                    is_active=True,
                    is_deleted=False,
                    images=[
                        "https://images.unsplash.com/photo-1531415074968-036ba1b575da?auto=format&fit=crop&w=1200&q=80",
                        "https://images.unsplash.com/photo-1540747913346-19e32dc3e97e?auto=format&fit=crop&w=1200&q=80",
                    ],
                )
                t2.facilities.set(fac_objs)
            else:
                t2.is_active = True
                t2.is_deleted = False
                t2.save(update_fields=["is_active", "is_deleted"])

            today = timezone.localtime(timezone.now()).date()
            for t in [t1, t2]:
                for day_offset in range(15):
                    d = today + timedelta(days=day_offset)
                    SchedulingEngine.generate_daily_slots(t, d)

            logger.info("Default primary turf arenas successfully auto-provisioned.")
    except Exception as err:
        logger.warning(f"Could not auto-provision default turfs on migrate: {err}")


class TurfsConfig(AppConfig):
    name = "turfs"

    def ready(self):
        post_migrate.connect(ensure_default_turfs, sender=self)
