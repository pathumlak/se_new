from decimal import Decimal

from django.db import migrations, models


def backfill_odometer_readings(apps, schema_editor):
    VehicleTrip = apps.get_model("core", "VehicleTrip")
    VehicleTrip.objects.update(start_km=Decimal("0.00"))
    for trip in VehicleTrip.objects.only("pk", "km").iterator():
        VehicleTrip.objects.filter(pk=trip.pk).update(end_km=trip.km)


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0029_order_delivered_at_alter_order_status"),
    ]

    operations = [
        migrations.AddField(
            model_name="vehicletrip",
            name="start_km",
            field=models.DecimalField(decimal_places=2, default=Decimal("0.00"), max_digits=10),
        ),
        migrations.AddField(
            model_name="vehicletrip",
            name="end_km",
            field=models.DecimalField(decimal_places=2, default=Decimal("0.00"), max_digits=10),
        ),
        migrations.RunPython(backfill_odometer_readings, migrations.RunPython.noop),
    ]
