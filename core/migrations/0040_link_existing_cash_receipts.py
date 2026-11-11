"""Link unambiguous generated receipts without changing dates or amounts."""
from collections import defaultdict
from django.db import migrations
from django.utils import timezone


def link_receipts(apps, schema_editor):
    Payment = apps.get_model('core', 'Payment')
    Drawer = apps.get_model('core', 'CashDrawer')
    db = schema_editor.connection.alias
    candidates = defaultdict(list)
    for payment in Payment.objects.using(db).filter(method='cash', supplier_bill__isnull=True).select_related('bill', 'customer'):
        if payment.bill_id:
            label = f'Bill #{payment.bill.bill_number or payment.bill_id}'
        elif payment.customer_id and not payment.customer.is_supplier:
            label = f'Settlement · {payment.customer.name}'
        else:
            continue
        dates = {timezone.localdate(payment.paid_at)}
        if payment.bill_id:
            dates.add(payment.bill.bill_date)  # Older drawer entries used bill date.
        for day in dates:
            candidates[(payment.bill_id, label+' cash', day, payment.amount)].append(payment.pk)
    used = set()
    for entry in Drawer.objects.using(db).filter(txn_type='in', payment__isnull=True).order_by('pk'):
        choices = candidates.get((entry.bill_id, entry.reason, entry.txn_date, entry.amount), [])
        if len(choices) != 1 or choices[0] in used:
            continue
        payment_id = choices[0]
        # Never choose between indistinguishable old receipts.
        duplicates = Drawer.objects.using(db).filter(txn_type='in', bill_id=entry.bill_id,
            reason=entry.reason, txn_date=entry.txn_date, amount=entry.amount, payment__isnull=True)
        if duplicates.count() != 1:
            continue
        Drawer.objects.using(db).filter(pk=entry.pk).update(payment_id=payment_id)
        used.add(payment_id)
        # A banked receipt has a matching outgoing leg, which must follow edits.
        transfers = Drawer.objects.using(db).filter(txn_type='transfer', bill_id=entry.bill_id,
            txn_date=entry.txn_date, amount=entry.amount, payment__isnull=True,
            reason__startswith=entry.reason+' to ')
        if transfers.count() == 1:
            transfers.update(payment_id=payment_id)


class Migration(migrations.Migration):
    dependencies = [('core', '0039_cashdrawer_payment')]
    operations = [migrations.RunPython(link_receipts, migrations.RunPython.noop)]
