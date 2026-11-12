import json
import re
import shutil
import subprocess
import uuid
from datetime import timedelta
from decimal import Decimal
from io import BytesIO
from unittest.mock import patch

from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from openpyxl import load_workbook

from .models import (Bill, CashDrawer, CashTransfer, Cheque, Customer,
                     CustomerSettlement, Payment, User)
from .views import _cash_drawer_balance, _ledger_rows


@override_settings(ALLOWED_HOSTS=['testserver'])
class AccountSettlementTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user('settlement-admin', role='super_admin')
        self.client.force_login(self.admin)
        self.day = timezone.localdate() - timedelta(days=4)
        self.customer = Customer.objects.create(name='Kasun', balance=-150000,
            opening_balance=0, opening_balance_date=self.day - timedelta(days=10))
        for amount in [50000, 60000, 40000]:
            Bill.objects.create(customer=self.customer, bill_date=self.day-timedelta(days=2),
                total_amount=amount, payment_type='pay_later', status='unpaid', balance_change=-amount)
        self.url = reverse('core:customer_settle', args=[self.customer.pk])

    def payload(self, **extra):
        data = {'request_token': str(uuid.uuid4()), 'method': 'cash', 'cash_amount': '140000',
            'settlement_date': self.day.isoformat(), 'note': '  Customer paid against previous outstanding\nExact note  ',
            'notice': 'Partial outstanding balance settlement', 'cheques_json': '[]'}
        data.update(extra)
        return data

    def cheque(self, amount='30000', **extra):
        value = {'cheque_no':'CH-100', 'bank_name':'BOC', 'amount':amount,
            'maturity_date':(self.day+timedelta(days=10)).isoformat()}
        value.update(extra)
        return value

    def record(self, **extra):
        response = self.client.post(self.url, self.payload(**extra))
        self.assertEqual(response.status_code, 302, response.content.decode()[:4000])
        return CustomerSettlement.objects.latest('pk')

    def assert_balance(self, balance, cash):
        self.customer.refresh_from_db()
        self.assertEqual(self.customer.balance, Decimal(balance))
        self.assertEqual(_cash_drawer_balance(), Decimal(cash))
        self.assertEqual(_ledger_rows(self.customer)[-1]['balance'], -Decimal(balance))

    def test_customer_level_example_does_not_touch_bills(self):
        bills = list(Bill.objects.values())
        s = self.record()
        self.assert_balance('-10000', '140000')
        self.assertEqual((s.previous_balance,s.updated_balance,s.amount), (-150000,-10000,140000))
        self.assertEqual(bills, list(Bill.objects.values()))
        p = Payment.objects.get()
        self.assertIsNone(p.bill_id)
        self.assertEqual(p.settlement_id, s.pk)
        self.assertEqual(timezone.localdate(p.paid_at), self.day)
        self.assertEqual(CashDrawer.objects.get().txn_date, self.day)
        for url in [reverse('core:customer_ledger',args=[self.customer.pk]), reverse('core:cash_drawer'), reverse('core:customer_settlement_detail',args=[s.pk])]:
            response = self.client.get(url, {'month':'all'})
            self.assertContains(response, s.note)
            self.assertContains(response, s.notice)
            self.assertContains(response, s.reference)
        report = self.client.get(reverse('core:outstanding_report'))
        self.assertEqual(report.context['total_received'], 140000)
        self.assertEqual(report.context['total_owed'], 10000)

    def test_mixed_receipt_groups_once_and_only_cash_affects_drawer(self):
        s = self.record(method='mixed', cash_amount='50000', bank_amount='60000',
            bank_name='BOC', bank_account='senovka', bank_reference='BANK-REF',
            cheques_json=json.dumps([self.cheque()]))
        self.assert_balance('-10000', '50000')
        self.assertEqual(s.payments.count(), 3)
        self.assertFalse(s.payments.filter(bill__isnull=False).exists())
        self.assertEqual(Cheque.objects.get().received_date, self.day)
        response = self.client.get(reverse('core:cash_drawer'), {'month':'all'})
        self.assertEqual(len(response.context['rows']), 1)
        self.assertEqual(response.context['total_in'], 50000)
        self.assertEqual(response.context['non_cash_total'], 90000)
        self.assertEqual(response.context['rows'][0]['entry'].amount, 140000)
        self.assertContains(response, 'BANK-REF')
        self.assertContains(response, 'CH-100')
        credits = [r for r in _ledger_rows(self.customer) if r.get('payment_members')]
        self.assertEqual(len(credits), 1)
        self.assertEqual(len(credits[0]['payment_members']), 1)
        self.assertEqual(credits[0]['credit'], 140000)
        ws = load_workbook(BytesIO(self.client.get(reverse('core:cash_drawer_excel'), {'month':'all'}).content)).active
        row = next(r for r in ws.iter_rows(values_only=True) if s.reference in str(r[1]))
        self.assertEqual((row[2],row[4],row[6]), (50000,50000,90000))
        self.assertIn(s.note, row[1]); self.assertIn(s.notice, row[1])

    def test_bank_only_and_cheque_only_do_not_change_physical_cash(self):
        self.record(method='transfer', cash_amount='', bank_amount='10000', bank_name='BOC',
            bank_account='senovka', bank_reference='TR1')
        self.record(method='cheque', cash_amount='', cheques_json=json.dumps([self.cheque('20000')]))
        self.assert_balance('-120000','0')
        self.assertEqual(CashTransfer.objects.get().amount, 10000)
        self.assertEqual(len(self.client.get(reverse('core:cash_drawer'), {'month':'all'}).context['rows']),1)

    def test_mixed_accepts_cash_bank_and_cheque_bank_combinations(self):
        for cash, cheque in [('1000',''),('','1000')]:
            self.record(method='mixed', cash_amount=cash, bank_amount='2000', bank_name='BOC',
                bank_account='senovka', bank_reference='TR', cheques_json=json.dumps([self.cheque(cheque)]) if cheque else '[]')
        self.assert_balance('-144000','1000')

    def test_same_day_settlements_group_but_preserve_both_notes(self):
        first=self.record(cash_amount='1000', note='First note')
        second=self.record(cash_amount='2000', note='Second note')
        self.record(cash_amount='3000', settlement_date=(self.day+timedelta(days=1)).isoformat())
        rows=[r for r in _ledger_rows(self.customer) if r.get('payment_members')]
        self.assertEqual([r['credit'] for r in rows], [3000,3000])
        self.assertEqual(len(rows[0]['payment_members']),2)
        response=self.client.get(reverse('core:customer_ledger', args=[self.customer.pk]), {'month':'all'})
        self.assertContains(response, first.note); self.assertContains(response, second.note)
        self.assertEqual(CustomerSettlement.objects.count(),3)

    def test_mandatory_fields_and_invalid_amounts_cannot_write(self):
        for field in ['note','notice','method','settlement_date','request_token']:
            data=self.payload(); data[field]=''
            response=self.client.post(self.url,data)
            self.assertEqual(response.status_code,200)
        for value in ['NaN','Infinity','-1','0','1.234']:
            self.assertEqual(self.client.post(self.url,self.payload(cash_amount=value)).status_code,200)
        for data in [self.payload(note='  '), self.payload(settlement_date=(timezone.localdate()+timedelta(days=1)).isoformat()),
                     self.payload(method='transfer',cash_amount='',bank_amount='1000'),
                     self.payload(method='cheque',cash_amount='',cheques_json=json.dumps([self.cheque('NaN')]))]:
            self.assertEqual(self.client.post(self.url,data).status_code,200)
        self.assertFalse(Payment.objects.exists()); self.assertFalse(CustomerSettlement.objects.exists())
        self.assert_balance('-150000','0')

    def test_duplicate_submission_is_idempotent(self):
        data=self.payload()
        first=self.client.post(self.url,data)
        second=self.client.post(self.url,data)
        self.assertEqual(first.url,second.url)
        self.assertEqual(CustomerSettlement.objects.count(),1)
        self.assertEqual(Payment.objects.count(),1)
        self.assert_balance('-10000','140000')

    def test_failed_drawer_write_rolls_back_everything(self):
        with patch('core.settlements.CashDrawer.objects.create',side_effect=RuntimeError('Simulated failure')):
            with self.assertRaises(RuntimeError):self.client.post(self.url,self.payload())
        self.assertFalse(CustomerSettlement.objects.exists());self.assertFalse(Payment.objects.exists())
        self.assert_balance('-150000','0')

    def test_correction_replaces_once_and_preserves_history(self):
        s=self.record()
        data=self.payload(cash_amount='130000',settlement_date=(self.day+timedelta(days=1)).isoformat(),
            note='Corrected note',notice='Corrected notice',correction_reason='Wrong amount and day')
        url=reverse('core:customer_settlement_edit',args=[s.pk])
        response=self.client.post(url,data)
        self.assertEqual(response.status_code,302)
        s.refresh_from_db(); self.assertEqual(s.status,'replaced')
        self.assertEqual(CustomerSettlement.objects.count(),2)
        self.assertEqual(Payment.objects.count(),1);self.assertEqual(Payment.all_objects.count(),2)
        self.assertEqual(CashDrawer.all_objects.count(),2)
        self.assert_balance('-20000','130000')
        self.assertEqual(CashDrawer.objects.get().txn_date,self.day+timedelta(days=1))
        self.assertEqual(self.client.post(url,data).status_code,302)
        self.assertEqual(CustomerSettlement.objects.count(),2)
        self.assertContains(self.client.get(reverse('core:customer_settlement_detail',args=[s.pk])),s.note)
        self.assertTrue(s.audits.filter(action='replaced', reason='Wrong amount and day').exists())

    def test_cancel_is_idempotent_and_keeps_payment_audit(self):
        s=self.record(method='mixed',cash_amount='10000',bank_amount='20000',bank_name='BOC',bank_account='senovka',bank_reference='R',cheques_json=json.dumps([self.cheque()]))
        url=reverse('core:customer_settlement_cancel',args=[s.pk])
        self.client.post(url,{'reason':'Entered twice'})
        self.client.post(url,{'reason':'Retry'})
        self.assert_balance('-150000','0')
        self.assertFalse(Payment.objects.exists());self.assertFalse(Cheque.objects.exists());self.assertFalse(CashTransfer.objects.exists())
        self.assertEqual(Payment.all_objects.count(),3);self.assertEqual(Cheque.all_objects.count(),1)
        self.assertEqual(s.audits.filter(action='cancelled').count(),1)
        detail=self.client.get(reverse('core:customer_settlement_detail',args=[s.pk]))
        self.assertContains(detail,'CH-100')
        self.assertContains(detail,'Entered twice')
        self.assertEqual(self.client.get(reverse('core:outstanding_report')).context['total_received'],0)

    def test_cheque_statuses_and_cancellation_do_not_double_reverse(self):
        s=self.record(method='cheque',cash_amount='',cheques_json=json.dumps([self.cheque('140000')]))
        cheque=Cheque.objects.get()
        for route,balance in [('cheque_hold','-10000'),('cheque_deposit','-10000'),('cheque_bounce','-150000')]:
            response=self.client.post(reverse('core:'+route,args=[cheque.pk]),{'maturity_date':(self.day+timedelta(days=15)).isoformat(), 'bounce_new_date':(self.day+timedelta(days=20)).isoformat()})
            self.assertEqual(response.status_code,302)
            self.assert_balance(balance,'0')
        self.assertEqual(s.audits.filter(action='cheque_status',actor=self.admin).count(),3)
        self.client.post(reverse('core:customer_settlement_cancel',args=[s.pk]),{'reason':'Returned receipt cancelled'})
        self.assert_balance('-150000','0')

    def test_correction_keeps_returned_cheque_state(self):
        s=self.record(method='cheque',cash_amount='',cheques_json=json.dumps([self.cheque('140000')]))
        cheque=Cheque.objects.get()
        self.client.post(reverse('core:cheque_bounce',args=[cheque.pk]), {'bounce_new_date':timezone.localdate().isoformat()})
        response=self.client.post(reverse('core:customer_settlement_edit',args=[s.pk]),self.payload(method='cheque',cash_amount='',
            cheques_json=json.dumps([self.cheque('130000',original_cheque_id=cheque.pk)]),correction_reason='Correct amount'))
        self.assertEqual(response.status_code,302)
        self.assertEqual(Cheque.objects.get().status,'bounced')
        self.assert_balance('-150000','0')

    def test_legacy_edit_routes_cannot_desynchronize_settlement(self):
        s=self.record(method='cheque',cash_amount='',cheques_json=json.dumps([self.cheque()]))
        p=Payment.objects.get(); cheque=Cheque.objects.get()
        self.client.post(reverse('core:customer_payment_edit',args=[self.customer.pk,p.pk]),{'amount':'1','paid_date':self.day.isoformat(),'reason':'Try'})
        self.client.post(reverse('core:cheque_delete',args=[cheque.pk]))
        self.client.post(reverse('core:cheque_edit',args=[cheque.pk]),{'amount':'1'})
        self.assertEqual(Payment.objects.get().amount,30000)
        self.assert_balance('-120000','0')

    def test_form_and_detail_render_valid_scripts_and_keep_inputs_on_error(self):
        data=self.payload(method='mixed',cash_amount='1000',note='',cheques_json=json.dumps([self.cheque()]))
        response=self.client.post(self.url,data)
        self.assertContains(response,'CH-100')
        node=shutil.which('node')
        if node:
            for attrs,body in re.findall(r'<script([^>]*)>(.*?)</script>',response.content.decode(),re.S):
                if 'application/json' in attrs or not body.strip():continue
                result=subprocess.run([node,'--check'],input=body,text=True,capture_output=True)
                self.assertEqual(result.returncode,0,result.stderr)

    def test_supplier_cannot_receive_customer_settlement(self):
        supplier=Customer.objects.create(name='Supplier',is_supplier=True)
        self.assertEqual(self.client.post(reverse('core:customer_settle',args=[supplier.pk]),self.payload()).status_code,404)

    def test_settlement_actions_respect_manager_task_permissions(self):
        from .models import PermissionAssignment, PermissionDefinition
        s=self.record(cash_amount='1000')
        manager=User.objects.create_user('settlement-manager',role='manager')
        self.client.force_login(manager)
        create=self.client.post(self.url,self.payload(cash_amount='1000'))
        self.assertEqual(create.status_code,403)
        PermissionAssignment.objects.create(user=manager,permission=PermissionDefinition.objects.get(codename='balances.view'),granted_by=self.admin)
        self.assertEqual(self.client.get(reverse('core:customer_settlement_detail',args=[s.pk])).status_code,200)
        self.assertEqual(self.client.post(reverse('core:customer_settlement_cancel',args=[s.pk]),{'reason':'Not authorized'}).status_code,403)
        self.assertEqual(self.client.get(reverse('core:customer_settlement_edit',args=[s.pk])).status_code,403)
        self.assert_balance('-149000','1000')

    def test_cash_sent_to_bank_has_two_legs_and_no_net_drawer_change(self):
        s=self.record(cash_amount='1000',cash_account='senovka')
        self.assert_balance('-149000','0')
        self.assertEqual(CashDrawer.objects.count(),2)
        self.assertEqual(CashTransfer.objects.get().payment.settlement_id,s.pk)
        self.assertEqual(CashTransfer.objects.get().amount,1000)

    def test_ledger_export_retains_both_grouped_settlement_notes(self):
        first=self.record(cash_amount='1000',note='First receipt note')
        second=self.record(cash_amount='2000',note='Second receipt note')
        from .views import _write_customer_ledger_sheet
        from openpyxl import Workbook
        wb=Workbook();self.customer.refresh_from_db()
        _write_customer_ledger_sheet(wb.active,self.customer,None,None)
        text=' '.join(str(c.value) for row in wb.active for c in row)
        for value in [first.reference,second.reference,first.note,second.note,first.notice]:
            self.assertIn(value,text)

    def test_invalid_correction_leaves_original_receipt_intact(self):
        s=self.record()
        self.client.post(reverse('core:customer_settlement_edit',args=[s.pk]),self.payload(note='',correction_reason='Try'))
        self.client.post(reverse('core:customer_settlement_cancel',args=[s.pk]),{'reason':'   '})
        self.assertEqual(CustomerSettlement.objects.count(),1)
        s.refresh_from_db();self.assertEqual(s.status,'active')
        self.assert_balance('-10000','140000')

    def test_overpayment_remains_customer_credit_without_bill_allocation(self):
        self.record(cash_amount='160000')
        self.assert_balance('10000','160000')
        self.assertFalse(Payment.objects.filter(bill__isnull=False).exists())
