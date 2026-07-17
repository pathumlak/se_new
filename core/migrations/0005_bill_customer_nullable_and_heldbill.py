# Hand-written to unblock walk-in bill saves: Bill.customer needs to be
# nullable in the database as well as the model, otherwise inserting a walk-in
# row (customer_id NULL) fails at the DB level and the biller sees a generic
# "Could not save the bill" error.
#
# HeldBill is added in the same migration since both are prerequisites of the
# hold-and-recall feature.

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0004_customer_is_walk_in_account"),
    ]

    operations = [
        migrations.AlterField(
            model_name="bill",
            name="customer",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="bills",
                to="core.customer",
            ),
        ),
        migrations.CreateModel(
            name="HeldBill",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("walk_in_name", models.CharField(blank=True, max_length=255)),
                ("payload", models.JSONField()),
                ("label", models.CharField(blank=True, max_length=255)),
                ("item_count", models.PositiveIntegerField(default=0)),
                (
                    "subtotal",
                    models.DecimalField(decimal_places=2, default=0, max_digits=12),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "customer",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="held_bills",
                        to="core.customer",
                    ),
                ),
                (
                    "created_by",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="held_bills",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "ordering": ["-updated_at", "-id"],
            },
        ),
    ]
