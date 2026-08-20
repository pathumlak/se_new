from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0020_backfill_customer_created_at"),
    ]

    operations = [
        migrations.CreateModel(
            name="PaymentEditAudit",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("original_date", models.DateField()),
                ("original_amount", models.DecimalField(decimal_places=2, max_digits=12)),
                ("new_date", models.DateField()),
                ("new_amount", models.DecimalField(decimal_places=2, max_digits=12)),
                ("reason", models.CharField(max_length=500)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("edited_by", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="payment_edit_audits", to="core.user")),
                ("payment", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="edit_audits", to="core.payment")),
            ],
            options={"ordering": ["-created_at", "-id"]},
        ),
    ]