import json
from decimal import Decimal

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.db.models import Prefetch
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from .decorators import task_permission_required
from .forms import CustomerSettlementForm
from .models import Customer, CustomerSettlement, Payment, Cheque
from .settlements import cancel_settlement, record_settlement, settlement_effect


def _payment_details(settlement):
    return Payment.all_objects.filter(settlement=settlement).prefetch_related(Prefetch('cheques', queryset=Cheque.all_objects.all())).order_by('pk')


def _form_page(request, customer, original=None):
    initial = {}
    if original:
        initial = dict(method=original.method, settlement_date=original.settlement_date,
            note=original.note, notice=original.notice, bank_name=original.bank_name,
            bank_reference=original.bank_reference)
        cheques = []
        for p in _payment_details(original):
            if p.method == 'cash':
                initial.update(cash_amount=p.amount, cash_account=p.account)
            elif p.method == 'transfer':
                initial.update(bank_amount=p.amount, bank_account=p.account)
            else:
                for cheque in p.cheques.all():
                    cheques.append(dict(original_cheque_id=cheque.pk, **{name: str(getattr(cheque, name)) for name in (
                        'cheque_no','bank_name','branch','acc_no','amount','maturity_date')}))
        initial['cheques_json'] = json.dumps(cheques)
    form = CustomerSettlementForm(request.POST if request.method == 'POST' else None,
        customer=customer, initial=initial, correcting=bool(original))
    if request.method == 'POST' and form.is_valid():
        try:
            settlement, created = record_settlement(customer.pk, form.cleaned_data,
                form.parsed_cheques, request.user, replaces=original.pk if original else None)
        except ValidationError as exc:
            form.add_error(None, exc)
        else:
            messages.success(request, f'{settlement.reference}: settlement recorded.' if created
                else f'{settlement.reference} was already recorded; no duplicate payment was created.')
            return redirect('core:customer_settlement_detail', pk=settlement.pk)
    raw = form['cheques_json'].value() or '[]'
    try:
        initial_cheques = json.loads(raw)
        if not isinstance(initial_cheques, list): initial_cheques = []
    except (ValueError, TypeError):
        initial_cheques = []
    return render(request, 'core/customer_settle.html', {
        'customer': customer, 'form': form, 'original': original,
        'total_owed': max(-(customer.balance - (settlement_effect(original) if original else 0)), Decimal('0')), 'initial_cheques': initial_cheques})


@task_permission_required
def customer_settle(request, pk):
    return _form_page(request, get_object_or_404(Customer, pk=pk, is_supplier=False))


@task_permission_required
def customer_settlement_detail(request, pk):
    settlement = get_object_or_404(CustomerSettlement.objects.select_related('customer','created_by','replaces'), pk=pk)
    return render(request, 'core/customer_settlement_detail.html', {
        'settlement': settlement, 'payments': _payment_details(settlement),
        'audits': settlement.audits.select_related('actor'),
        'replacement': CustomerSettlement.objects.filter(replaces=settlement).first()})


@task_permission_required
def customer_settlement_edit(request, pk):
    settlement = get_object_or_404(CustomerSettlement.objects.select_related('customer'), pk=pk)
    if settlement.status != CustomerSettlement.Status.ACTIVE:
        messages.error(request, 'This settlement has already been cancelled or corrected.')
        return redirect('core:customer_settlement_detail', pk=pk)
    return _form_page(request, settlement.customer, settlement)


@require_POST
@task_permission_required
def customer_settlement_cancel(request, pk):
    get_object_or_404(CustomerSettlement, pk=pk)
    try:
        cancel_settlement(pk, request.user, request.POST.get('reason', ''))
    except ValidationError as exc:
        messages.error(request, ' '.join(exc.messages))
    else:
        messages.success(request, 'Settlement cancelled. Account, ledger and drawer have been updated.')
    return redirect('core:customer_settlement_detail', pk=pk)
