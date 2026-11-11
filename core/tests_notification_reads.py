from django.test import TestCase, Client, override_settings
from django.urls import reverse
from .models import User, Category, Product, PermissionAssignment, PermissionDefinition


@override_settings(ALLOWED_HOSTS=['testserver'])
class NotificationReadTests(TestCase):
    def setUp(self):
        self.user=User.objects.create_user('reader',password='test-password',role='super_admin')
        self.other=User.objects.create_user('other',role='super_admin')
        self.client.force_login(self.user)
        category=Category.objects.create(name='Alerts')
        self.product=Product.objects.create(name='Low item',qty=1,category=category)
        self.url=reverse('core:notification_dismiss')

    def feed(self):
        return self.client.get(reverse('core:profile')).context

    def mark(self):
        return self.client.post(self.url,{'action':'read_all'})

    def test_mark_all_clears_badge_but_keeps_notifications(self):
        before=self.feed()
        self.assertGreater(before['notification_count'],0)
        self.assertContains(self.client.get('/profile/'),'Mark all as read')
        self.assertEqual(self.mark().json(),{'ok':True,'unread_count':0})
        after=self.feed()
        self.assertEqual(after['notification_count'],0)
        self.assertEqual(len(after['notifications']),len(before['notifications']))
        self.assertTrue(all(item['is_read'] for item in after['notifications']))

    def test_read_state_persists_across_login_and_devices(self):
        self.mark()
        self.client.logout()
        self.client.login(username='reader',password='test-password')
        self.assertEqual(self.feed()['notification_count'],0)
        second=Client();second.force_login(self.user)
        self.assertEqual(second.get('/profile/').context['notification_count'],0)

    def test_does_not_change_other_accounts(self):
        self.mark()
        self.client.force_login(self.other)
        self.assertGreater(self.feed()['notification_count'],0)
        self.other.refresh_from_db()
        self.assertEqual(self.other.notification_reads,{})

    def test_new_and_changed_notifications_become_unread(self):
        self.mark()
        self.product.qty=2;self.product.save()
        self.assertEqual(self.feed()['notification_count'],1)
        self.mark()
        Product.objects.create(name='New low item',category=self.product.category,qty=1)
        self.assertEqual(self.feed()['notification_count'],1)

    def test_manager_only_marks_notifications_they_can_access(self):
        manager=User.objects.create_user('manager',role='manager')
        self.client.force_login(manager)
        self.mark();manager.refresh_from_db()
        self.assertEqual(manager.notification_reads,{})
        PermissionAssignment.objects.create(user=manager,
            permission=PermissionDefinition.objects.get(codename='pricing.view'),granted_by=self.user)
        self.mark();manager.refresh_from_db()
        self.assertTrue(manager.notification_reads)

    def test_post_only_authenticated_and_csrf_protected(self):
        self.assertEqual(self.client.get(self.url).status_code,405)
        protected=Client(enforce_csrf_checks=True);protected.force_login(self.user)
        self.assertEqual(protected.post(self.url,{'action':'read_all'}).status_code,403)
        self.client.logout()
        self.assertEqual(self.mark().status_code,302)
        self.user.refresh_from_db();self.assertEqual(self.user.notification_reads,{})

    def test_no_notifications_and_repeat_clicks_are_safe(self):
        self.product.qty=1000000;self.product.save()
        self.assertEqual(self.mark().status_code,200)
        self.assertEqual(self.mark().status_code,200)
        self.assertEqual(self.feed()['notification_count'],0)
