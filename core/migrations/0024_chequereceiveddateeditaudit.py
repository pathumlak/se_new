from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0023_bill_bill_number"),
    ]

    operations = [
        migrations.CreateModel(
            name="ChequeReceivedDateEditAudit",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("original_date", models.DateField()),
                ("new_date", models.DateField()),
                ("reason", models.CharField(max_length=500)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("cheque", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="received_date_edit_audits", to="core.cheque")),
                ("edited_by", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="cheque_received_date_edit_audits", to="core.user")),
            ],
            options={"ordering": ["-created_at", "-id"]},
        ),
    ]
