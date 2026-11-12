"""Daily receipt presentation. Never merge or save accounting records here."""
from copy import copy
from decimal import Decimal

ZERO = Decimal('0')


def consolidate_ledger_payments(entries):
    groups = {}
    result = []
    for entry in entries:
        if not (entry.get('payment_pk') or entry.get('settlement')) or entry.get('credit') is None:
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
        group['payment_pks'].extend(entry.get('payment_ids', [entry.get('payment_pk')]))
    for group in groups.values():
        if len(group['payment_members']) > 1:
            group['description'] = f"Payments received · {len(group['payment_members'])} records"
            group['payment_pk'] = None
            group['payment_editable'] = False
    for row in result:
        parts = []
        for member in row.get('payment_members', [row]):
            text = member['description']
            settlement = member.get('settlement')
            if settlement:
                text += (f" · {settlement.method_label} · Note: {settlement.note}"
                         f" · Notice: {settlement.notice}"
                         f" · Available balance at recording: {settlement.previous_balance} → {settlement.updated_balance}")
                for cheque in settlement.cheque_records:
                    text += f" · Cheque {cheque.cheque_no} / {cheque.bank_name} / {cheque.get_status_display()} / {cheque.amount}"
                if settlement.bank_reference:
                    text += f" · {settlement.bank_name} / {settlement.bank_reference}"
            parts.append(text)
        row['export_description'] = "\n".join(parts)
    return result


def cash_drawer_rows(entries, opening=ZERO):
    """Group customer receipts and transfers separately by day and destination."""
    rows, groups = [], {}
    for entry in entries:
        customer = entry.bill.customer if entry.bill_id and entry.bill.customer_id else None
        if customer is None and entry.payment_id:
            payment = entry.payment
            # Detached supplier payments are outgoing, never customer receipts.
            if payment.customer_id and not payment.supplier_bill_id and not payment.customer.is_supplier:
                customer = payment.customer
        is_receipt = customer is not None and entry.txn_type in {'in', 'bank', 'cheque'}
        destination = ""
        if customer is not None and entry.txn_type == 'transfer':
            if entry.payment_id and entry.payment.account:
                destination = entry.payment.get_account_display()
            elif entry.bill_id:
                # Older generated transfers may predate the payment link.
                prefix = f"Bill #{entry.bill.bill_number or entry.bill_id} cash to "
                if entry.reason.startswith(prefix):
                    destination = entry.reason[len(prefix):].strip()
        is_transfer = bool(destination)
        if is_receipt:
            key = (customer.pk, entry.txn_date, 'in', '')
        elif is_transfer:
            key = (customer.pk, entry.txn_date, 'transfer', destination.casefold())
        else:
            key = None
        if key is not None and key in groups:
            row = groups[key]
            row['entry'].amount += entry.amount
            row['source_entries'].append(entry)
        else:
            row = {'entry': copy(entry), 'source_entries': [entry],
                   'customer': customer if is_receipt or is_transfer else None,
                   'is_transfer': is_transfer, 'destination': destination,
                   'is_in': entry.txn_type in {'in', 'bank', 'cheque'},
                   'is_manual': not (entry.bill_id or entry.supplier_bill_id or entry.payment_id)}
            rows.append(row)
            if key is not None:
                groups[key] = row
    running, total_in, total_out = opening, ZERO, ZERO
    for row in rows:
        entry = row['entry']
        row['cash_in'] = sum((item.amount for item in row['source_entries'] if item.txn_type == 'in'), ZERO)
        row['cash_out'] = sum((item.amount for item in row['source_entries'] if item.txn_type in {'out', 'transfer'}), ZERO)
        row['non_cash'] = sum((item.amount for item in row['source_entries'] if item.txn_type in {'bank', 'cheque'}), ZERO)
        row['is_grouped'] = len(row['source_entries']) > 1
        if row['customer']:
            movement = f"Transferred to {row['destination']}" if row['is_transfer'] else ('Payments received' if row['non_cash'] else 'Cash received')
            row['description'] = f"{row['customer'].name} · {movement}"
            if row['is_grouped']:
                row['description'] += f" · {len(row['source_entries'])} records"
        else:
            row['description'] = entry.reason or entry.get_txn_type_display()
        row['search_text'] = row['description'] + ' ' + ' '.join(f"{item.reason} Payment #{item.payment_id or ''}" for item in row['source_entries'])
        total_in += row['cash_in']
        total_out += row['cash_out']
        running += row['cash_in'] - row['cash_out']
        settlements = {item.payment.settlement_id: item.payment.settlement for item in row['source_entries']
                       if item.payment_id and item.payment.settlement_id}
        row['settlements'] = list(settlements.values())
        row['search_text'] += ' ' + ' '.join(f"{item.reference} {item.note} {item.notice} {item.bank_reference}" for item in row['settlements'])
        row['running'] = running
    return rows, total_in, total_out, running
