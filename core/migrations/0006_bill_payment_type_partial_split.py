# The pre-split "Partial" payment type — cash + cheque covering the full
# bill — has been broken into two distinct types: PARTIAL_CASH (cash for
# part of the bill, remainder outstanding) and PARTIAL_CHEQUE (cheques for
# part of the bill, remainder outstanding). MIXED remains cash + cheque for
# the full total. The old "partial" value is kept in the choices so rows
# written before this migration still render.

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0005_bill_customer_nullable_and_heldbill"),
    ]

    operations = [
        migrations.AlterField(
            model_name="bill",
            name="payment_type",
            field=models.CharField(
                choices=[
                    ("full_cash", "Full Cash"),
                    ("full_cheque", "Full Cheque"),
                    ("partial", "Partial (legacy)"),
                    ("partial_cash", "Partial Cash"),
                    ("partial_cheque", "Partial Cheque"),
                    ("mixed", "Mixed"),
                    ("pay_later", "Pay Later"),
                ],
                max_length=20,
            ),
        ),
    ]
