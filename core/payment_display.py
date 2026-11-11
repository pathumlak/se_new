"""Daily receipt presentation. Never merge or save accounting records here."""
from copy import copy
from decimal import Decimal

ZERO = Decimal('0')


def consolidate_ledger_payments(entries):
    groups = {}
    result = []
    for entry in entries:
        if not entry.get('payment_pk') or entry.get('credit') is None:
            result.append(entry)
            continue
        key = entry['date']  # Caller already scopes to one customer.
        if key not in groups:
            group = dict(entry, payment_members=[], payment_pks=[])
            groups[key] = group
            result.append(group)
        else:
            group = groups[key]
            group['credit'] += entry['credit']
        group['payment_members'].append(dict(entry))
        group['payment_pks'].append(entry['payment_pk'])
    for group in groups.values():
        if len(group['payment_members']) > 1:
            group['description'] = f"Payments received · {len(group['payment_members'])} records"
            group['payment_pk'] = None
            group['payment_editable'] = False
    return result


def cash_drawer_rows(entries, opening=ZERO):
    """Group only customer cash receipts; expenses and bank transfers stay separate."""
    rows, groups = [], {}
    for entry in entries:
        customer = entry.bill.customer if entry.bill_id and entry.bill.customer_id else None
        if customer is None and entry.payment_id:
            payment = entry.payment
            # Detached supplier payments are outgoing, never customer receipts.
            if payment.customer_id and not payment.supplier_bill_id and not payment.customer.is_supplier:
                customer = payment.customer
        is_receipt = customer is not None and entry.txn_type == 'in'
        key = (customer.pk, entry.txn_date) if is_receipt else None
        if key is not None and key in groups:
            row = groups[key]
            row['entry'].amount += entry.amount
            row['source_entries'].append(entry)
        else:
            row = {'entry': copy(entry), 'source_entries': [entry],
                   'customer': customer if is_receipt else None,
                   'is_in': entry.txn_type == 'in',
                   'is_manual': not (entry.bill_id or entry.supplier_bill_id or entry.payment_id)}
            rows.append(row)
            if key is not None:
                groups[key] = row
    running, total_in, total_out = opening, ZERO, ZERO
    for row in rows:
        entry = row['entry']
        row['is_grouped'] = len(row['source_entries']) > 1
        if row['customer']:
            row['description'] = f"{row['customer'].name} · Cash received"
            if row['is_grouped']:
                row['description'] += f" · {len(row['source_entries'])} records"
        else:
            row['description'] = entry.reason or entry.get_txn_type_display()
        row['search_text'] = row['description'] + ' ' + ' '.join(f"{item.reason} Payment #{item.payment_id or ''}" for item in row['source_entries'])
        if row['is_in']:
            total_in += entry.amount
            running += entry.amount
        else:
            total_out += entry.amount
            running -= entry.amount
        row['running'] = running
    return rows, total_in, total_out, running
