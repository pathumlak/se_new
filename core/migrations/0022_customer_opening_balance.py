from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0021_paymenteditaudit"),
    ]

    operations = [
        migrations.AddField(
            model_name="customer",
            name="opening_balance",
            field=models.DecimalField(blank=True, decimal_places=2, max_digits=12, null=True),
        ),
        migrations.AddField(
            model_name="customer",
            name="opening_balance_date",
            field=models.DateField(blank=True, null=True),
        ),
        migrations.CreateModel(
            name="CustomerOpeningBalanceEditAudit",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("original_date", models.DateField()),
                ("original_amount", models.DecimalField(decimal_places=2, max_digits=12)),
                ("new_date", models.DateField()),
                ("new_amount", models.DecimalField(decimal_places=2, max_digits=12)),
                ("reason", models.CharField(max_length=500)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("customer", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="opening_balance_edit_audits", to="core.customer")),
                ("edited_by", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="opening_balance_edit_audits", to="core.user")),
            ],
            options={"ordering": ["-created_at", "-id"]},
        ),
    ]