from django.db import migrations


def realign_all_active_turfs(apps, schema_editor):
    from turfs.models import Turf
    from turfs.services import SchedulingEngine

    for turf in Turf.objects.filter(is_active=True, is_deleted=False):
        try:
            SchedulingEngine.realign_future_slots(turf, days_ahead=15)
        except Exception as e:
            print(f"Warning: could not realign slots for turf {turf.id}: {e}")


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("turfs", "0006_alter_timeslot_status"),
    ]

    operations = [
        migrations.RunPython(realign_all_active_turfs, reverse_code=noop),
    ]
