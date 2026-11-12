import uuid
from datetime import date, timedelta
from decimal import Decimal
from io import BytesIO
from importlib import import_module
from django.apps import apps
from django.db import connection
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from openpyxl import load_workbook
from .models import (User, Customer, Category, Product, Bill, Payment, CashDrawer, CashTransfer, Cheque)
from .views import (_save_bill, _ledger_rows, _record_payments,
                    _cash_drawer_balance, _customer_ledger_period_slice)
from .payment_display import cash_drawer_rows


@override_settings(ALLOWED_HOSTS=['testserver'])
class DailyReceiptTests(TestCase):
    def setUp(self):
        self.admin=User.objects.create_user('receipt-admin',role='super_admin')
        self.client.force_login(self.admin)
        self.day=timezone.localdate()-timedelta(days=6)
        self.customer=Customer.objects.create(name='Same customer',credit_limit=10000)
        cat=Category.objects.create(name='Receipt test')
        self.product=Product.objects.create(name='Item',category=cat,default_price=10,qty=1000)

    def bill(self,total=100):
        return _save_bill(self.admin, {'customer_id':self.customer.pk,
            'bill_date':(self.day-timedelta(days=5)).isoformat(),
            'lines':[{'product_id':self.product.pk,'qty':str(total//10),'unit_price':'10'}],
            'payment':{'type':'pay_later'}})

    def pay(self,bill,amount,day=None,**extra):
        payload={'method':'cash','amount':str(amount),'payment_date':(day or self.day).isoformat()}
        payload.update(extra)
        response=self.client.post(reverse('core:bill_add_payment',args=[bill.pk]),payload)
        self.assertEqual(response.status_code,302,response.content)

    def drawer_rows(self):
        entries=CashDrawer.objects.select_related('bill__customer','payment__customer').order_by('txn_date','pk')
        return cash_drawer_rows(entries)

    def credits(self):
        return [r for r in _ledger_rows(self.customer) if r.get('payment_members')]

    def test_same_day_bill_payment_and_multi_bill_settlement_are_one_row(self):
        first=self.bill(100);second=self.bill(200)
        self.pay(first,40)
        response=self.client.post(reverse('core:customer_settle',args=[self.customer.pk]),{
            'request_token':str(uuid.uuid4()),'note':'Against outstanding','notice':'Account payment',
            'method':'cash','cash_amount':'280','settlement_date':self.day.isoformat()})
        self.assertEqual(response.status_code,302)
        # One bill payment and one customer-level settlement; bills remain unchanged.
        self.assertEqual(list(Payment.objects.order_by('pk').values_list('amount',flat=True)),[40,280])
        self.assertEqual(len(self.credits()),1)
        self.assertEqual(self.credits()[0]['credit'],320)
        self.assertEqual(len(self.credits()[0]['payment_members']),2)
        rows,total_in,total_out,closing=self.drawer_rows()
        self.assertEqual((len(rows),total_in,total_out,closing),(1,320,0,320))
        self.assertFalse(rows[0]['is_manual'])
        first.refresh_from_db();second.refresh_from_db()
        self.assertEqual((first.remaining_balance,second.remaining_balance),(60,200))
        self.customer.refresh_from_db();self.assertEqual(self.customer.balance,20)
        self.assertEqual(_ledger_rows(self.customer)[-1]['balance'],-20)
        self.assertEqual(CashDrawer.objects.count(),2)

    def test_different_dates_and_customers_remain_separate(self):
        bill=self.bill(100)
        self.pay(bill,20);self.pay(bill,30,self.day+timedelta(days=1))
        other=Customer.objects.create(name=self.customer.name)
        _record_payments(None,other,{'cash':Decimal(15),'cash_account':'','cheques':[]},paid_on=self.day)
        self.assertEqual(len(self.credits()),2)
        self.assertEqual(len(self.drawer_rows()[0]),3)
        bill.refresh_from_db();self.assertEqual(bill.remaining_balance,50)

    def test_selected_follow_up_date_drives_both_records_not_bill_date(self):
        bill=self.bill();self.pay(bill,25)
        payment=Payment.objects.get()
        self.assertEqual(timezone.localdate(payment.paid_at),self.day)
        self.assertEqual(payment.drawer_entries.get().txn_date,self.day)
        self.assertEqual(self.credits()[0]['date'],self.day)
        self.assertNotEqual(bill.bill_date,self.day)

    def test_initial_bill_cash_payment_date_is_independent_of_bill_date(self):
        payload={'customer_id':self.customer.pk,'bill_date':(self.day-timedelta(days=5)).isoformat(),
            'payment_date':self.day.isoformat(),'lines':[{'product_id':self.product.pk,'qty':'10','unit_price':'10'}],
            'payment':{'type':'full_cash','cash':'100'}}
        response=self.client.post(reverse('core:bill_save'),payload,content_type='application/json')
        self.assertEqual(response.status_code,200,response.content)
        self.assertEqual(timezone.localdate(Payment.objects.get().paid_at),self.day)
        self.assertEqual(CashDrawer.objects.get().txn_date,self.day)

    def test_invalid_payment_dates_write_nothing(self):
        bill=self.bill()
        for raw in ['', 'not-a-date', (timezone.localdate()+timedelta(days=1)).isoformat()]:
            response=self.client.post(reverse('core:bill_add_payment',args=[bill.pk]),{'method':'cash','amount':'20','payment_date':raw})
            self.assertEqual(response.status_code,200)
            self.assertEqual(Payment.objects.count(),0)
            self.assertEqual(CashDrawer.objects.count(),0)
        bill.refresh_from_db();self.assertEqual(bill.remaining_balance,100)

    def test_bank_transfer_expense_and_manual_cash_are_not_folded_into_receipts(self):
        bill=self.bill();self.pay(bill,20,cash_account='senovka');self.pay(bill,10)
        CashDrawer.objects.create(txn_date=self.day,txn_type='out',amount=3,reason='Expense')
        CashDrawer.objects.create(txn_date=self.day,txn_type='in',amount=7,reason='Float')
        rows,total_in,total_out,closing=self.drawer_rows()
        self.assertEqual((len(rows),total_in,total_out,closing),(4,37,23,14))
        self.assertEqual(_cash_drawer_balance(),14)
        self.assertEqual(CashTransfer.objects.get().amount,20)

    def test_group_details_keep_individual_edit_and_date_changes_in_sync(self):
        bill=self.bill();self.pay(bill,20);self.pay(bill,30)
        payment=Payment.objects.order_by('pk').first()
        response=self.client.get(reverse('core:customer_ledger',args=[self.customer.pk]),{'month':'all','edit_payment':payment.pk})
        self.assertContains(response,f'Edit individual payment #{payment.pk}')
        response=self.client.post(reverse('core:customer_payment_edit',args=[self.customer.pk,payment.pk]),{
            'paid_date':(self.day+timedelta(days=1)).isoformat(),'amount':'25','reason':'Correct receipt'})
        self.assertEqual(response.status_code,302)
        payment.refresh_from_db();bill.refresh_from_db()
        self.assertEqual(payment.drawer_entries.get().amount,25)
        self.assertEqual(payment.drawer_entries.get().txn_date,self.day+timedelta(days=1))
        self.assertEqual(bill.remaining_balance,45)
        self.assertEqual(len(self.credits()),2)
        self.assertEqual(len(self.drawer_rows()[0]),2)
        self.assertEqual(Payment.objects.count(),2)

    def test_grouping_does_not_mutate_records_and_exports_keep_totals(self):
        bill=self.bill();self.pay(bill,20);self.pay(bill,30)
        before=list(Payment.objects.values())
        drawer_before=list(CashDrawer.objects.values())
        for _ in range(2):
            self.credits();self.drawer_rows()
            response=self.client.get(reverse('core:cash_drawer_excel'),{'month':'all'})
            self.assertEqual(response.status_code,200)
            ws=load_workbook(BytesIO(response.content)).active
            receipt=[row for row in ws.iter_rows(values_only=True) if row[1] and 'Cash received' in str(row[1])]
            self.assertEqual(len(receipt),1);self.assertEqual(receipt[0][2],50)
        self.assertEqual(before,list(Payment.objects.values()))
        self.assertEqual(drawer_before,list(CashDrawer.objects.values()))

    def test_grouping_before_pagination_and_filtered_opening_balance(self):
        bill=self.bill(500)
        self.pay(bill,10,self.day-timedelta(days=1))
        for _ in range(3):self.pay(bill,20)
        with override_settings(PAGINATE_BY_REPORTS=1):
            response=self.client.get(reverse('core:cash_drawer'),{'from_date':self.day.isoformat(),'to_date':self.day.isoformat()})
            self.assertEqual(response.context['page_obj'].paginator.count,1)
            self.assertEqual(response.context['opening'],10)
            self.assertEqual(response.context['closing'],70)
            self.assertEqual(response.context['total_in'],60)
        rows,stats=_customer_ledger_period_slice(_ledger_rows(self.customer),self.day,self.day)
        self.assertEqual(len(rows),1);self.assertEqual(stats['total_credit'],60)

    def test_cheque_and_cash_share_received_date_but_only_cash_enters_drawer(self):
        bill=self.bill();self.pay(bill,20)
        self.pay(bill,30,method='cheque',cheque_no='R123',bank_name='Bank',maturity_date=(self.day+timedelta(days=10)).isoformat())
        self.assertEqual(Cheque.objects.get().received_date,self.day)
        self.assertEqual(self.credits()[0]['credit'],50)
        self.assertEqual(len(self.credits()),1)
        self.assertEqual(self.drawer_rows()[1],20)

    def test_link_migration_preserves_legacy_dates_and_amounts(self):
        bill=self.bill();self.pay(bill,20)
        entry=CashDrawer.objects.get();entry.payment=None;entry.save()
        before=list(CashDrawer.objects.values_list('pk','txn_date','amount'))
        migration=import_module('core.migrations.0040_link_existing_cash_receipts')
        migration.link_receipts(apps,connection.schema_editor())
        entry.refresh_from_db();self.assertIsNotNone(entry.payment_id)
        self.assertEqual(before,list(CashDrawer.objects.values_list('pk','txn_date','amount')))

    def test_bank_transfer_edit_updates_both_legs_without_net_cash_change(self):
        bill=self.bill();self.pay(bill,20,cash_account='senovka')
        payment=Payment.objects.get()
        response=self.client.post(reverse('core:customer_payment_edit',args=[self.customer.pk,payment.pk]),{
            'paid_date':self.day.isoformat(),'amount':'25','reason':'Receipt correction'})
        self.assertEqual(response.status_code,302)
        self.assertEqual(list(payment.drawer_entries.values_list('amount',flat=True)),[25,25])
        self.assertEqual(_cash_drawer_balance(),0)
        self.assertEqual(CashTransfer.objects.get().amount,25)

    def test_repeatedly_viewing_reports_does_not_duplicate_payment_or_change_balance(self):
        bill=self.bill();self.pay(bill,20);self.pay(bill,30)
        before=(list(Bill.objects.values()),list(Customer.objects.values()),list(Payment.objects.values()))
        for route in ['sales_report','outstanding_report','cash_drawer']:
            response=self.client.get(reverse('core:'+route),{'month':'all'})
            self.assertEqual(response.status_code,200)
        self.assertEqual(before,(list(Bill.objects.values()),list(Customer.objects.values()),list(Payment.objects.values())))
        bill.refresh_from_db();self.assertEqual(bill.remaining_balance,50)

    def test_customer_returns_and_adjustments_do_not_join_receipt_group(self):
        from .models import CustomerBalanceAdjustment
        bill=self.bill();self.pay(bill,20);self.pay(bill,30)
        CustomerBalanceAdjustment.objects.create(customer=self.customer,adjustment_type='credit',
            amount=5,adjustment_date=self.day,reason='Correction',adjusted_by=self.admin)
        rows=_ledger_rows(self.customer)
        self.assertEqual(len([r for r in rows if r.get('payment_members')]),1)
        self.assertEqual(len([r for r in rows if r.get('is_adjustment')]),1)

    def test_ambiguous_legacy_receipts_are_not_linked_by_guessing(self):
        bill=self.bill();self.pay(bill,20);self.pay(bill,20)
        CashDrawer.objects.update(payment=None)
        migration=import_module('core.migrations.0040_link_existing_cash_receipts')
        migration.link_receipts(apps,connection.schema_editor())
        self.assertFalse(CashDrawer.objects.filter(payment__isnull=False).exists())
        # Known bill/customer identity still permits display consolidation.
        self.assertEqual(len(self.drawer_rows()[0]),1)

    def test_payment_screens_render_valid_scripts_and_date_controls(self):
        import re, subprocess, shutil
        node = shutil.which("node")
        bill=self.bill();self.pay(bill,20);self.pay(bill,30)
        pages=[(reverse('core:bill_create'),'id="payment-date"'),
               (reverse('core:bill_add_payment',args=[bill.pk]),'name="payment_date"'),
               (reverse('core:customer_ledger',args=[self.customer.pk])+'?month=all','View individual payments'),
               (reverse('core:cash_drawer')+'?month=all','View receipt records')]
        for url,expected in pages:
            response=self.client.get(url)
            self.assertContains(response,expected)
            for attrs,body in re.findall(r'<script([^>]*)>(.*?)</script>',response.content.decode(),re.S):
                if not node or 'application/json' in attrs or not body.strip():continue
                checked=subprocess.run([node,'--check'],input=body,text=True,capture_output=True)
                self.assertEqual(checked.returncode,0,checked.stderr)

    def test_screenshot_case_consolidates_receipts_and_bank_transfers_separately(self):
        self.customer.credit_limit = 200000
        self.customer.save(update_fields=['credit_limit'])
        first=self.bill(93250);second=self.bill(8750)
        self.pay(first,93250,cash_account='senovka')
        self.pay(second,8750,cash_account='senovka')
        _record_payments(None,self.customer,{'cash':Decimal(48000),'cash_account':'senovka','cheques':[]},paid_on=self.day)
        before=list(CashDrawer.objects.values())
        entries=CashDrawer.objects.select_related('bill__customer','payment__customer').order_by('txn_date','pk')
        rows,total_in,total_out,closing=cash_drawer_rows(entries,Decimal(37322))
        self.assertEqual(len(rows),2)
        receipt,transfer=rows
        self.assertEqual(receipt['entry'].amount,150000)
        self.assertEqual(transfer['entry'].amount,150000)
        self.assertTrue(transfer['is_transfer'])
        self.assertIn('Transferred to Senovka',transfer['description'])
        self.assertEqual((receipt['running'],transfer['running']),(187322,37322))
        self.assertEqual((total_in,total_out,closing),(150000,150000,37322))
        self.assertEqual(len(transfer['source_entries']),3)
        self.assertEqual(before,list(CashDrawer.objects.values()))
        response=self.client.get(reverse('core:cash_drawer'),{'month':'all'})
        self.assertContains(response,'class="js-drawer-row"',count=2)
        self.assertContains(response,'View transfer records')
        export=self.client.get(reverse('core:cash_drawer_excel'),{'month':'all'})
        ws=load_workbook(BytesIO(export.content)).active
        outgoing=[r for r in ws.iter_rows(values_only=True) if 'Transferred to Senovka' in str(r[1])]
        self.assertEqual(len(outgoing),1)
        self.assertEqual(outgoing[0][3],150000)

    def test_transfers_with_different_accounts_dates_or_customers_stay_separate(self):
        first=self.bill();self.pay(first,10,cash_account='senovka');self.pay(first,10,cash_account='senovka')
        self.pay(first,10,self.day+timedelta(days=1),cash_account='senovka')
        other=Customer.objects.create(name=self.customer.name)
        _record_payments(None,other,{'cash':Decimal(5),'cash_account':'senovka','cheques':[]},paid_on=self.day)
        # Retained legacy account values still must not mix with Senovka.
        _record_payments(None,self.customer,{'cash':Decimal(7),'cash_account':'dinusha','cheques':[]},paid_on=self.day)
        transfers=[r for r in self.drawer_rows()[0] if r['is_transfer']]
        self.assertEqual(len(transfers),4)
        self.assertEqual(sorted(r['entry'].amount for r in transfers),[5,7,10,20])

    def test_legacy_bill_transfers_group_without_a_payment_link(self):
        first=self.bill();second=self.bill()
        self.pay(first,20,cash_account='senovka');self.pay(second,30,cash_account='senovka')
        CashDrawer.objects.filter(txn_type='transfer').update(payment=None)
        transfers=[r for r in self.drawer_rows()[0] if r['is_transfer']]
        self.assertEqual(len(transfers),1)
        self.assertEqual(transfers[0]['entry'].amount,50)
