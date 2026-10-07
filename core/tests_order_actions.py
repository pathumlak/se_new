"""Order Book list: Edit / Cancel / Delete on each quotation row, and returning
to the same list page afterwards."""

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from .models import Order

User = get_user_model()


class OrderListActionTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = User.objects.create_user(username="adm", password="pw", role=User.Role.SUPER_ADMIN)
        cls.manager = User.objects.create_user(username="mgr", password="pw", role=User.Role.MANAGER)
        cls.today = timezone.localdate()

    def order(self, status, name="Walk"):
        return Order.objects.create(
            order_date=self.today, customer_name=name, created_by=self.admin, status=status
        )

    def html(self, user):
        self.client.force_login(user)
        return self.client.get(reverse("core:order_list"), {"month": "all"}).content.decode()

    def test_open_orders_get_edit_and_cancel(self):
        for status in (Order.Status.DRAFT, Order.Status.SENT, Order.Status.CONFIRMED):
            order = self.order(status)
            html = self.html(self.manager)
            self.assertIn(reverse("core:order_edit", args=[order.pk]), html, status)
            self.assertIn(
                f'data-status-url="{reverse("core:order_set_status", args=[order.pk, "cancelled"])}"',
                html, status,
            )

    def test_delivered_and_cancelled_orders_are_not_editable_or_cancellable(self):
        delivered = self.order(Order.Status.DELIVERED)
        cancelled = self.order(Order.Status.CANCELLED)
        html = self.html(self.manager)
        for order in (delivered, cancelled):
            self.assertNotIn(reverse("core:order_edit", args=[order.pk]), html)
            self.assertNotIn(reverse("core:order_set_status", args=[order.pk, "cancelled"]), html)

    def test_delete_is_offered_to_super_admin_only(self):
        order = self.order(Order.Status.DRAFT)
        url = reverse("core:order_delete", args=[order.pk])
        self.assertIn(f'data-delete-url="{url}"', self.html(self.admin))
        manager_html = self.html(self.manager)
        self.assertNotIn(url, manager_html)
        self.assertNotIn('id="order-delete-modal"', manager_html)

    def test_manager_cannot_delete_by_posting(self):
        order = self.order(Order.Status.DRAFT)
        self.client.force_login(self.manager)
        self.client.post(reverse("core:order_delete", args=[order.pk]))
        self.assertTrue(Order.objects.filter(pk=order.pk).exists())

    def test_cancel_returns_to_the_list_page_it_came_from(self):
        order = self.order(Order.Status.DRAFT)
        self.client.force_login(self.manager)
        back = "/orders/?month=all&status=draft&page=2"
        response = self.client.post(
            reverse("core:order_set_status", args=[order.pk, "cancelled"]), {"next": back}
        )
        self.assertRedirects(response, back, fetch_redirect_response=False)
        order.refresh_from_db()
        self.assertEqual(order.status, Order.Status.CANCELLED)

    def test_status_change_without_next_still_goes_to_the_detail_page(self):
        order = self.order(Order.Status.DRAFT)
        self.client.force_login(self.manager)
        response = self.client.post(reverse("core:order_set_status", args=[order.pk, "sent"]))
        self.assertRedirects(response, reverse("core:order_detail", args=[order.pk]))

    def test_delete_returns_to_the_list_page_it_came_from(self):
        order = self.order(Order.Status.DRAFT)
        self.client.force_login(self.admin)
        back = "/orders/?month=all&page=3"
        response = self.client.post(reverse("core:order_delete", args=[order.pk]), {"next": back})
        self.assertRedirects(response, back, fetch_redirect_response=False)
        self.assertFalse(Order.objects.filter(pk=order.pk).exists())

    def test_delete_without_next_goes_to_the_list(self):
        order = self.order(Order.Status.DRAFT)
        self.client.force_login(self.admin)
        response = self.client.post(reverse("core:order_delete", args=[order.pk]))
        self.assertRedirects(response, reverse("core:order_list"))

    def test_off_site_next_is_ignored(self):
        self.client.force_login(self.manager)
        for evil in ("https://evil.example/x", "//evil.example/x"):
            order = self.order(Order.Status.DRAFT)
            response = self.client.post(
                reverse("core:order_set_status", args=[order.pk, "cancelled"]), {"next": evil}
            )
            self.assertRedirects(response, reverse("core:order_detail", args=[order.pk]))

    def test_list_forms_carry_the_current_page_as_next(self):
        self.order(Order.Status.DRAFT)
        html = self.html(self.admin)
        self.assertIn('name="next" value="/orders/?month=all"', html)

    def test_detail_page_offers_edit_on_a_confirmed_quotation(self):
        order = self.order(Order.Status.CONFIRMED)
        self.client.force_login(self.manager)
        html = self.client.get(reverse("core:order_detail", args=[order.pk])).content.decode()
        self.assertIn(reverse("core:order_edit", args=[order.pk]), html)
