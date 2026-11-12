"""Atomic customer-account settlements and their audit trail."""
from datetime import datetime, time
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import F
from django.http import Http404
from django.utils import timezone

from .models import (CashDrawer, CashTransfer, Cheque, Customer, CustomerSettlement,
                     Payment, SettlementAudit)

ZERO = Decimal('0')


def settlement_effect(settlement):
    """Current account credit, following the existing pending/held cheque rules."""
    return sum((p.amount for p in Payment.all_objects.filter(settlement=settlement)
                if not p.cheques.filter(status=Cheque.Status.BOUNCED).exists()), ZERO)


def _void_locked(settlement, customer, actor, reason, status):
    if settlement.status != CustomerSettlement.Status.ACTIVE:
        return
    amount = settlement_effect(settlement)
    before = customer.balance
    Customer.objects.filter(pk=customer.pk).update(balance=F('balance') - amount)
    customer.balance -= amount
    settlement.status = status
    settlement.save(update_fields=['status'])
    SettlementAudit.objects.create(settlement=settlement, actor=actor,
        action=status, reason=reason, details={
            'balance_before': str(before), 'balance_after': str(customer.balance),
            'credit_removed': str(amount)})


@transaction.atomic
def record_settlement(customer_id, data, cheques, actor, replaces=None):
    customer = Customer.objects.select_for_update().get(pk=customer_id, is_supplier=False)
    existing = CustomerSettlement.objects.filter(request_token=data['request_token']).first()
    if existing:
        if existing.customer_id != customer_id or existing.replaces_id != replaces:
            raise ValidationError('This submission reference has already been used.')
        return existing, False
    original = None
    balance = customer.balance
    if replaces:
        original = CustomerSettlement.objects.select_for_update().get(pk=replaces, customer=customer)
        if original.status != CustomerSettlement.Status.ACTIVE:
            raise ValidationError('This settlement has already been cancelled or corrected. Reload the ledger.')
        balance -= settlement_effect(original)
    original_cheques = {c.pk: c for c in Cheque.all_objects.filter(payment__settlement=original)} if original else {}
    used_ids = set()
    prepared_cheques = []
    credited = data['_cash_amount'] + data['_bank_amount']
    for values in cheques:
        item = dict(values)
        old_id = item.pop('_original_id', None)
        if old_id:
            if old_id not in original_cheques or old_id in used_ids:
                raise ValidationError('The original cheque reference is invalid or repeated.')
            used_ids.add(old_id)
            old = original_cheques[old_id]
            if old.bounced_at and item['received_date'] > old.bounced_at:
                raise ValidationError('A returned cheque cannot be received after its return date.')
            item.update(status=old.status, bounced_at=old.bounced_at, bounce_new_date=old.bounce_new_date)
        if item.get('status') != Cheque.Status.BOUNCED:
            credited += item['amount']
        prepared_cheques.append(item)
    if credited < max(-balance, ZERO) and not actor.has_erp_permission('billing.partial_payments'):
        raise ValidationError('You do not have permission to record a partial payment.')
    if original:
        _void_locked(original, customer, actor, data['correction_reason'], CustomerSettlement.Status.REPLACED)
    settlement = CustomerSettlement.objects.create(customer=customer,
        request_token=data['request_token'], settlement_date=data['settlement_date'],
        method=data['method'], amount=data['_total_paid'], note=data['note'], notice=data['notice'],
        bank_name=data.get('bank_name') or '', bank_reference=data.get('bank_reference') or '',
        previous_balance=customer.balance, updated_balance=customer.balance + credited,
        replaces=original, created_by=actor)
    stamp = timezone.make_aware(datetime.combine(settlement.settlement_date, time.min))

    def payment(method, amount, account=''):
        return Payment.objects.create(customer=customer, settlement=settlement, method=method,
            amount=amount, account=account, paid_at=stamp)

    def drawer(p, kind):
        return CashDrawer.objects.create(payment=p, txn_date=settlement.settlement_date,
            txn_type=kind, amount=p.amount, reason=f'Outstanding Balance Settlement {settlement.reference}')

    cash = data['_cash_amount']
    if cash:
        p = payment(Payment.Method.CASH, cash, data.get('cash_account') or '')
        drawer(p, CashDrawer.TxnType.IN)
        if p.account:
            CashTransfer.objects.create(payment=p, to_account=p.account, amount=cash, transferred_at=stamp)
            drawer(p, CashDrawer.TxnType.TRANSFER)
    bank = data['_bank_amount']
    if bank:
        p = payment(Payment.Method.TRANSFER, bank, data['bank_account'])
        CashTransfer.objects.create(payment=p, to_account=p.account, amount=bank, transferred_at=stamp)
        drawer(p, CashDrawer.TxnType.BANK)
    for item in prepared_cheques:
        p = payment(Payment.Method.CHEQUE, item['amount'])
        Cheque.objects.create(payment=p, customer=customer, **item)
        drawer(p, CashDrawer.TxnType.CHEQUE)
    Customer.objects.filter(pk=customer.pk).update(balance=F('balance') + credited)
    SettlementAudit.objects.create(settlement=settlement, actor=actor, action='created',
        reason=data.get('correction_reason') or 'Settlement recorded', details={
            'balance_before': str(settlement.previous_balance),
            'balance_after': str(settlement.updated_balance),
            'amount': str(settlement.amount), 'date': str(settlement.settlement_date),
            'replaces': original.reference if original else None})
    return settlement, True


@transaction.atomic
def cancel_settlement(settlement_id, actor, reason):
    if not reason.strip():
        raise ValidationError('Enter a reason for cancelling this settlement.')
    initial = CustomerSettlement.objects.get(pk=settlement_id)
    customer = Customer.objects.select_for_update().get(pk=initial.customer_id)
    settlement = CustomerSettlement.objects.select_for_update().get(pk=settlement_id)
    _void_locked(settlement, customer, actor, reason, CustomerSettlement.Status.CANCELLED)
    return settlement


@transaction.atomic
def change_settlement_cheque_status(cheque_id, status, actor=None, bounce_new_date=None, maturity_date=None):
    initial = Cheque.all_objects.select_related('payment').get(pk=cheque_id)
    Customer.objects.select_for_update().get(pk=initial.customer_id)
    settlement = CustomerSettlement.objects.select_for_update().get(pk=initial.payment.settlement_id)
    if settlement.status != CustomerSettlement.Status.ACTIVE:
        raise Http404('This settlement is no longer active.')
    cheque = Cheque.objects.select_for_update().select_related('customer').get(pk=cheque_id)
    old_status = cheque.status
    if status == old_status:
        return cheque, None
    before = ZERO if old_status == Cheque.Status.BOUNCED else cheque.amount
    after = ZERO if status == Cheque.Status.BOUNCED else cheque.amount
    delta = after - before
    cheque.status = status
    cheque.bounced_at = timezone.localdate() if status == Cheque.Status.BOUNCED else None
    if bounce_new_date is not None:
        cheque.bounce_new_date = bounce_new_date
    if maturity_date is not None:
        cheque.maturity_date = maturity_date
    cheque.save()
    Customer.objects.filter(pk=cheque.customer_id).update(balance=F('balance') + delta)
    SettlementAudit.objects.create(settlement=settlement, actor=actor, action='cheque_status',
        reason=f'Cheque {cheque.cheque_no}: {old_status} to {status}', details={
            'cheque': cheque.pk, 'from': old_status, 'to': status, 'balance_change': str(delta),
            'maturity_date': str(cheque.maturity_date), 'return_date': str(cheque.bounced_at or '')})
    return cheque, delta
