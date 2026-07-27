# Back-fill created_at for customers that existed before this field was added.
#
# Every customer already in the table at deploy time was carried over at
# go-live, so their account-opening date is the system-start date (1 Jul 2026),
# not the moment this migration happens to run. auto_now_add only knows "now",
# which would wrongly stamp them with the deploy date, so we set them by hand
# here. New customers created after this migration keep their real auto_now_add
# timestamp.

from datetime import datetime

from django.db import migrations
from django.utils import timezone


def set_created_at(apps, schema_editor):
    Customer = apps.get_model("core", "Customer")
    start = timezone.make_aware(datetime(2026, 7, 1, 0, 0, 0))
    Customer.objects.filter(created_at__isnull=True).update(created_at=start)


def noop(apps, schema_editor):
    # Nothing to undo: created_at simply goes back to null on reverse.
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0019_customer_created_at"),
    ]

    operations = [
        migrations.RunPython(set_created_at, noop),
    ]
