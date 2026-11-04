"""Task delegation must agree across administration, navigation and HTTP writes."""
from datetime import date
from decimal import Decimal
from django.test import TestCase, override_settings
from django.urls import reverse
from .models import (User, PermissionDefinition, PermissionAssignment, PermissionAudit,
                     PermissionTemplate, Category, Product, Customer, CashDrawer, Bill)
from .permissions import can_access, ASSIGNABLE_CODES, URL_PERMISSION_MAP, TASK_SPECS


@override_settings(ALLOWED_HOSTS=['testserver'])
class TaskPermissionTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = User.objects.create_user('task-admin', role='super_admin')
        cls.manager = User.objects.create_user('task-manager', role='manager')
        cls.other = User.objects.create_user('other-manager', role='manager')
        cls.category = Category.objects.create(name='Test category')
        cls.product = Product.objects.create(name='Test product', category=cls.category, default_price=10)
        cls.customer = Customer.objects.create(name='Test customer', balance=Decimal('30'))

    def setUp(self):
        self.client.force_login(self.manager)

    def grant(self, *codes):
        for code in codes:
            PermissionAssignment.objects.get_or_create(user=self.manager,
                permission=PermissionDefinition.objects.get(codename=code), defaults={'granted_by':self.admin})

    def test_every_assignable_code_has_an_active_definition(self):
        self.assertEqual(set(PermissionDefinition.objects.filter(is_active=True, codename__in=ASSIGNABLE_CODES).values_list('codename', flat=True)), ASSIGNABLE_CODES)

    def test_category_task_overrides_old_admin_only_gate(self):
        self.grant('categories.view', 'categories.create')
        response = self.client.post(reverse('core:category_create'), {'name':'Delegated', 'description':''})
        self.assertEqual(response.status_code, 302)
        self.assertTrue(Category.objects.filter(name='Delegated').exists())
        self.assertEqual(self.client.post(reverse('core:category_delete', args=[self.category.pk])).status_code,403)

    def test_granted_delete_executes_and_revocation_is_immediate(self):
        self.grant('products.delete')
        response = self.client.post(reverse('core:product_delete', args=[self.product.pk]))
        self.assertEqual(response.status_code, 302)
        self.assertFalse(Product.objects.filter(pk=self.product.pk).exists())
        self.manager.erp_permission_assignments.all().delete()
        self.assertEqual(self.client.post(reverse('core:product_delete',args=[999])).status_code,403)

    def test_all_granular_tasks_are_isolated_from_sibling_tasks(self):
        for prefix, module, broad, model, operations in TASK_SPECS:
            for operation, routes in operations.items():
                with self.subTest(task=f'{prefix}.{operation}'):
                    self.manager.erp_permission_assignments.all().delete()
                    self.grant(f'{prefix}.{operation}')
                    for route in routes.split():
                        self.assertTrue(can_access(self.manager, route))
                    for other_op, other_routes in operations.items():
                        if other_op != operation:
                            for route in other_routes.split():
                                self.assertFalse(can_access(self.manager, route))

    def test_legacy_create_does_not_grant_edit_or_delete(self):
        self.grant('data.category.create')
        self.assertTrue(can_access(self.manager,'category_create'))
        self.assertFalse(can_access(self.manager,'category_update'))
        self.assertFalse(can_access(self.manager,'category_delete'))

    def test_view_customer_contacts_cannot_post_edits(self):
        self.grant('customers.view')
        url=reverse('core:customer_contacts')+f'?pk={self.customer.pk}'
        self.assertEqual(self.client.get(url).status_code,200)
        self.assertEqual(self.client.post(url,{'name':'forged'}).status_code,403)
        self.customer.refresh_from_db()
        self.assertEqual(self.customer.name,'Test customer')
        self.grant('customers.edit')
        self.assertEqual(self.client.post(url,{'name':'allowed'}).status_code,200)
        self.customer.refresh_from_db()
        self.assertEqual(self.customer.name,'allowed')

    def test_cash_view_cannot_post_outgoing_cash(self):
        self.grant('cash_drawer.view')
        self.assertEqual(self.client.post(reverse('core:cash_drawer'),{'amount':'10'}).status_code,403)
        self.assertEqual(CashDrawer.objects.count(),0)
        self.grant('cash_drawer.create')
        self.assertTrue(can_access(self.manager,'cash_drawer','POST'))

    def test_export_requires_permission_and_source_scope(self):
        self.grant('reports.export')
        self.assertEqual(self.client.get(reverse('core:customer_list_excel')).status_code,403)
        self.grant('customers.view')
        self.assertEqual(self.client.get(reverse('core:customer_list_excel')).status_code,200)
        self.manager.erp_permission_assignments.filter(permission__codename='reports.export').delete()
        self.assertEqual(self.client.get(reverse('core:customer_list_excel')).status_code,403)

    def test_view_only_navigation_hides_writes_and_unrelated_modules(self):
        self.grant('products.view')
        response=self.client.get(reverse('core:product_list'))
        self.assertContains(response,'Find a section')
        self.assertNotContains(response,'href="/products/create/"')
        self.assertNotContains(response,'href="/orders/"')
        self.assertNotContains(response,'href="/cash-drawer/"')
        self.assertNotContains(response,'data-delete-url=')

    def test_no_dashboard_grant_lands_on_profile(self):
        self.assertRedirects(self.client.get('/dashboard/'),reverse('core:profile'))

    def test_manager_cannot_assign_their_own_permissions(self):
        self.grant('user_management.manage')
        self.assertEqual(self.client.post(reverse('core:manager_permissions_for_user',args=[self.manager.pk]),{'permissions':['billing.delete']}).status_code,403)
        self.assertFalse(self.manager.has_erp_permission('billing.delete'))

    def test_admin_assignment_is_targeted_audited_and_adds_required_views(self):
        self.client.force_login(self.admin)
        response=self.client.post(reverse('core:manager_permissions_for_user',args=[self.manager.pk]),{'permissions':['categories.create','database.manage','made.up']})
        self.assertEqual(response.status_code,302)
        self.assertTrue(self.manager.has_erp_permission('categories.create'))
        self.assertTrue(self.manager.has_erp_permission('categories.view'))
        self.assertFalse(self.other.has_erp_permission('categories.create'))
        self.assertFalse(self.manager.has_erp_permission('database.manage'))
        self.assertEqual(PermissionAudit.objects.filter(target_user=self.manager).count(),2)
        self.client.post(reverse('core:manager_permissions_for_user',args=[self.manager.pk]),{})
        self.assertEqual(self.manager.erp_permission_assignments.count(),0)
        self.assertEqual(PermissionAudit.objects.filter(target_user=self.manager,action='revoke').count(),2)

    def test_permission_screen_only_offers_real_manager_tasks(self):
        self.client.force_login(self.admin)
        response=self.client.get(reverse('core:manager_permissions_for_user',args=[self.manager.pk]))
        self.assertContains(response,'value="production.create"')
        for code in ['data.billitem.delete','database.manage','payments.delete','balances.set']:
            self.assertNotContains(response,f'value="{code}"')

    def test_customer_edit_does_not_allow_forged_balance(self):
        self.grant('customers.edit')
        response=self.client.post(reverse('core:customer_update',args=[self.customer.pk]),{
            'name':'Updated','phone':'','email':'','address':'','balance':'99999','is_active':'on'})
        self.assertEqual(response.status_code,302)
        self.customer.refresh_from_db()
        self.assertEqual(self.customer.name,'Updated')
        self.assertEqual(self.customer.balance,Decimal('30'))

    def test_restricted_pages_do_not_leak_notifications_or_cash_balance(self):
        self.grant('products.view')
        response=self.client.get('/products/')
        self.assertIsNone(response.context['cash_balance'])
        self.assertEqual(response.context['notifications'],[])

    def test_duplicate_template_name_is_a_validation_error(self):
        self.client.force_login(self.admin)
        PermissionTemplate.objects.create(name='Sales tasks',created_by=self.admin)
        response=self.client.post(reverse('core:permission_templates'),{'name':'Sales tasks'})
        self.assertEqual(response.status_code,200)
        self.assertContains(response,'already exists')
        self.assertEqual(PermissionTemplate.objects.count(),1)

    def test_each_delegated_business_route_passes_old_role_checks(self):
        from .urls import urlpatterns
        for prefix, module, broad, model, operations in TASK_SPECS:
            for op, routes in operations.items():
                self.grant(f'{prefix}.{op}')
                for route in routes.split():
                    pattern=next(p for p in urlpatterns if p.name == route)
                    kwargs={key: ('confirmed' if key=='status' else 999999) for key in pattern.pattern.converters}
                    with self.subTest(route=route):
                        response=self.client.get(reverse('core:'+route,kwargs=kwargs))
                        self.assertNotEqual(response.status_code,403)
                        self.assertNotEqual(response.get('Location'),'/dashboard/')

    def test_bill_edit_can_load_product_feed_without_create_access(self):
        self.grant('billing.edit')
        response=self.client.get(reverse('core:bill_products',args=[self.customer.pk]))
        self.assertEqual(response.status_code,200)
        self.assertEqual(self.client.post(reverse('core:bill_save'),{},content_type='application/json').status_code,403)

    def bill_payload(self, payment):
        return {'customer_id':self.customer.pk,'bill_date':date.today().isoformat(),
                'lines':[{'product_id':self.product.pk,'qty':'10','unit_price':'10'}],
                'payment':payment}

    def test_paid_bill_requires_payment_task_and_rolls_back_on_denial(self):
        self.customer.balance=0
        self.customer.save()
        self.grant('billing.create')
        payload=self.bill_payload({'type':'full_cash','cash':'100'})
        response=self.client.post(reverse('core:bill_save'),payload,content_type='application/json')
        self.assertEqual(response.status_code,400)
        self.assertIn('permission',response.json()['error'])
        self.assertEqual(Bill.objects.count(),0)
        self.product.refresh_from_db()
        self.assertEqual(self.product.qty,0)
        self.grant('payments.create')
        response=self.client.post(reverse('core:bill_save'),payload,content_type='application/json')
        self.assertEqual(response.status_code,200,response.content)
        self.assertEqual(Bill.objects.count(),1)

    def test_existing_credit_requires_balance_use_permission(self):
        self.grant('billing.create','payments.create')
        payload=self.bill_payload({'type':'full_cash','cash':'70'})
        response=self.client.post(reverse('core:bill_save'),payload,content_type='application/json')
        self.assertEqual(response.status_code,400)
        self.assertEqual(Bill.objects.count(),0)
        self.grant('balances.use')
        response=self.client.post(reverse('core:bill_save'),payload,content_type='application/json')
        self.assertEqual(response.status_code,200,response.content)
        # Credit plus cash is a full payment, not a partial-payment operation.
        self.assertFalse(self.manager.has_erp_permission('billing.partial_payments'))

    def test_partial_payment_requires_its_own_task(self):
        self.customer.balance=0
        self.customer.save()
        self.grant('billing.create','payments.create')
        payload=self.bill_payload({'type':'partial_cash','cash':'20'})
        response=self.client.post(reverse('core:bill_save'),payload,content_type='application/json')
        self.assertEqual(response.status_code,400)
        self.assertEqual(Bill.objects.count(),0)
        self.grant('billing.partial_payments')
        response=self.client.post(reverse('core:bill_save'),payload,content_type='application/json')
        self.assertEqual(response.status_code,200,response.content)

    def test_bulk_customer_payment_requires_partial_task_when_underpaying(self):
        self.customer.balance=-100
        self.customer.save()
        self.grant('billing.settle')
        payload={'method':'cash','cash_amount':'20','settlement_date':date.today().isoformat()}
        url=reverse('core:customer_settle',args=[self.customer.pk])
        response=self.client.post(url,payload)
        self.assertContains(response,'permission to record a partial payment')
        self.customer.refresh_from_db()
        self.assertEqual(self.customer.balance,-100)
        self.grant('billing.partial_payments')
        self.assertEqual(self.client.post(url,payload).status_code,302)
        self.customer.refresh_from_db()
        self.assertEqual(self.customer.balance,-80)

    def test_permission_template_cannot_copy_reserved_grants(self):
        self.client.force_login(self.admin)
        template=PermissionTemplate.objects.create(name='Old template',created_by=self.admin)
        template.permissions.set(PermissionDefinition.objects.filter(codename__in=['database.manage','categories.create']))
        self.client.post(reverse('core:permission_template_assign',args=[template.pk]),{'manager':self.manager.pk})
        self.assertTrue(self.manager.has_erp_permission('categories.create'))
        self.assertTrue(self.manager.has_erp_permission('categories.view'))
        self.assertFalse(self.manager.has_erp_permission('database.manage'))
