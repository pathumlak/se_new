import json
import re
from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from core.models import (
    Bill,
    CashDrawer,
    Category,
    Cheque,
    Customer,
    CustomerPrice,
    Payment,
    Product,
)

User = get_user_model()

# Every sidebar destination, and whether a manager may open it.
NAV_URL_NAMES = [
    ("core:dashboard", True),
    ("core:category_list", False),  # super_admin only
    ("core:product_list", True),
    ("core:customer_list", True),
    ("core:make_bill", True),
    ("core:bill_list", True),
    ("core:cheque_list", True),
    ("core:cash_drawer", True),
    ("core:supplier_bill_list", True),
    ("core:production", True),
    ("core:customer_ledger", True),
    ("core:sales_report", True),
]


class UserFactoryMixin:
    def make_admin(self):
        return User.objects.create_user(
            username="t_admin", password="pw", role=User.Role.SUPER_ADMIN
        )

    def make_manager(self):
        return User.objects.create_user(
            username="t_manager", password="pw", role=User.Role.MANAGER
        )


class AuthFlowTests(UserFactoryMixin, TestCase):
    def test_dashboard_redirects_anonymous_to_login(self):
        response = self.client.get(reverse("core:dashboard"))
        self.assertRedirects(
            response, f"{reverse('login')}?next={reverse('core:dashboard')}"
        )

    def test_login_redirects_to_dashboard(self):
        self.make_manager()
        response = self.client.post(
            reverse("login"), {"username": "t_manager", "password": "pw"}
        )
        self.assertRedirects(response, "/dashboard/")

    def test_logout_redirects_to_login(self):
        self.client.force_login(self.make_manager())
        response = self.client.post(reverse("logout"))
        self.assertRedirects(response, "/login/")

    def test_root_redirects_to_dashboard(self):
        self.client.force_login(self.make_manager())
        self.assertRedirects(self.client.get("/"), "/dashboard/")

    def test_no_registration_route_exists(self):
        for path in ("/register/", "/signup/", "/accounts/register/"):
            self.assertEqual(self.client.get(path).status_code, 404, path)


class NavigationTests(UserFactoryMixin, TestCase):
    def test_every_nav_url_resolves_and_renders_for_admin(self):
        self.client.force_login(self.make_admin())
        for name, _ in NAV_URL_NAMES:
            with self.subTest(url=name):
                self.assertEqual(self.client.get(reverse(name)).status_code, 200)

    def test_all_nav_urls_require_login(self):
        for name, _ in NAV_URL_NAMES:
            with self.subTest(url=name):
                response = self.client.get(reverse(name))
                self.assertEqual(response.status_code, 302)
                self.assertIn(reverse("login"), response["Location"])

    def test_manager_access_matches_policy(self):
        """Allowed pages render; super_admin-only pages redirect to dashboard."""
        self.client.force_login(self.make_manager())
        for name, manager_allowed in NAV_URL_NAMES:
            with self.subTest(url=name):
                response = self.client.get(reverse(name))
                if manager_allowed:
                    self.assertEqual(response.status_code, 200)
                else:
                    self.assertRedirects(response, reverse("core:dashboard"))

    def test_active_item_highlighted_only_once(self):
        """Make Bill (/bills/new/) must not also light up Bill List (/bills/)."""
        self.client.force_login(self.make_admin())
        html = self.client.get(reverse("core:make_bill")).content.decode()
        self.assertEqual(html.count("bg-slate-800 text-white"), 1)

    def test_bill_list_active_state_is_distinct(self):
        self.client.force_login(self.make_admin())
        html = self.client.get(reverse("core:bill_list")).content.decode()
        self.assertEqual(html.count("bg-slate-800 text-white"), 1)

    def test_categories_link_hidden_from_manager(self):
        self.client.force_login(self.make_manager())
        html = self.client.get(reverse("core:dashboard")).content.decode()
        self.assertNotIn(reverse("core:category_list"), html)

    def test_categories_link_shown_to_admin(self):
        self.client.force_login(self.make_admin())
        html = self.client.get(reverse("core:dashboard")).content.decode()
        self.assertIn(reverse("core:category_list"), html)


class TopBarTests(UserFactoryMixin, TestCase):
    def test_topbar_shows_username_and_role_badge(self):
        self.client.force_login(self.make_admin())
        html = self.client.get(reverse("core:dashboard")).content.decode()
        self.assertIn("t_admin", html)
        self.assertIn("Super Admin", html)

    def test_topbar_has_logout_form(self):
        self.client.force_login(self.make_manager())
        html = self.client.get(reverse("core:dashboard")).content.decode()
        self.assertIn(reverse("logout"), html)
        self.assertIn("csrfmiddlewaretoken", html)


class RoleAccessTests(UserFactoryMixin, TestCase):
    def test_manager_redirected_to_dashboard_with_error(self):
        self.client.force_login(self.make_manager())
        response = self.client.get(reverse("core:category_list"), follow=True)
        self.assertRedirects(response, reverse("core:dashboard"))
        msgs = [str(m) for m in response.context["messages"]]
        self.assertEqual(msgs, ["You don't have permission to access that page."])

    def test_manager_blocked_from_every_category_url(self):
        self.client.force_login(self.make_manager())
        category = Category.objects.create(name="Guarded")
        cases = [
            ("get", reverse("core:category_list")),
            ("get", reverse("core:category_create")),
            ("post", reverse("core:category_create")),
            ("get", reverse("core:category_update", args=[category.pk])),
            ("post", reverse("core:category_update", args=[category.pk])),
            ("post", reverse("core:category_delete", args=[category.pk])),
        ]
        for method, url in cases:
            with self.subTest(method=method, url=url):
                response = getattr(self.client, method)(url, {})
                self.assertRedirects(response, reverse("core:dashboard"))
        # The guarded category survived every attempt.
        self.assertTrue(Category.objects.filter(pk=category.pk).exists())

    def test_anonymous_is_redirected_to_login(self):
        response = self.client.get(reverse("core:category_list"))
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("login"), response["Location"])


class CategoryCrudTests(UserFactoryMixin, TestCase):
    def setUp(self):
        self.client.force_login(self.make_admin())
        self.pipes = Category.objects.create(name="Pipes", description="Rigid PVC pipes")
        self.tanks = Category.objects.create(name="Tanks", description="Storage tanks")
        Product.objects.create(
            name="Pipe", size="50mm", category=self.pipes, qty=10, default_price=100
        )

    # ---- list ----
    def test_list_shows_categories_and_product_counts(self):
        response = self.client.get(reverse("core:category_list"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Pipes")
        self.assertContains(response, "Tanks")
        counts = {c.name: c.product_count for c in response.context["categories"]}
        self.assertEqual(counts, {"Pipes": 1, "Tanks": 0})

    def test_search_filters_by_name(self):
        response = self.client.get(reverse("core:category_list"), {"q": "pip"})
        names = [c.name for c in response.context["categories"]]
        self.assertEqual(names, ["Pipes"])

    def test_search_filters_by_description(self):
        response = self.client.get(reverse("core:category_list"), {"q": "storage"})
        names = [c.name for c in response.context["categories"]]
        self.assertEqual(names, ["Tanks"])

    def test_search_with_no_matches_renders_empty_state(self):
        response = self.client.get(reverse("core:category_list"), {"q": "zzzz"})
        self.assertEqual(list(response.context["categories"]), [])
        self.assertContains(response, "No categories match")

    # ---- create ----
    def test_create_page_renders(self):
        self.assertEqual(self.client.get(reverse("core:category_create")).status_code, 200)

    def test_create_saves_and_redirects_with_message(self):
        response = self.client.post(
            reverse("core:category_create"),
            {"name": "Fittings", "description": "Elbows and tees"},
            follow=True,
        )
        self.assertRedirects(response, reverse("core:category_list"))
        self.assertTrue(Category.objects.filter(name="Fittings").exists())
        msgs = [str(m) for m in response.context["messages"]]
        self.assertIn("Category 'Fittings' was created.", msgs)

    def test_create_rejects_duplicate_name(self):
        response = self.client.post(
            reverse("core:category_create"), {"name": "Pipes", "description": ""}
        )
        self.assertEqual(response.status_code, 200)  # re-renders, no redirect
        self.assertFormError(response.context["form"], "name", "Category with this Name already exists.")
        self.assertEqual(Category.objects.filter(name="Pipes").count(), 1)

    def test_create_rejects_blank_name(self):
        response = self.client.post(reverse("core:category_create"), {"name": "", "description": "x"})
        self.assertEqual(response.status_code, 200)
        self.assertFormError(response.context["form"], "name", "This field is required.")

    def test_create_strips_surrounding_whitespace(self):
        self.client.post(
            reverse("core:category_create"), {"name": "  Spaced  ", "description": ""}
        )
        self.assertTrue(Category.objects.filter(name="Spaced").exists())

    # ---- edit ----
    def test_edit_page_is_prefilled(self):
        response = self.client.get(reverse("core:category_update", args=[self.pipes.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["form"].initial["name"], "Pipes")
        self.assertTrue(response.context["is_edit"])

    def test_edit_saves_and_redirects_with_message(self):
        response = self.client.post(
            reverse("core:category_update", args=[self.pipes.pk]),
            {"name": "PVC Pipes", "description": "Updated"},
            follow=True,
        )
        self.assertRedirects(response, reverse("core:category_list"))
        self.pipes.refresh_from_db()
        self.assertEqual(self.pipes.name, "PVC Pipes")
        self.assertEqual(self.pipes.description, "Updated")
        msgs = [str(m) for m in response.context["messages"]]
        self.assertIn("Category 'PVC Pipes' was updated.", msgs)

    def test_edit_missing_category_404s(self):
        self.assertEqual(
            self.client.get(reverse("core:category_update", args=[9999])).status_code, 404
        )

    # ---- delete ----
    def test_delete_empty_category_succeeds(self):
        response = self.client.post(
            reverse("core:category_delete", args=[self.tanks.pk]), follow=True
        )
        self.assertRedirects(response, reverse("core:category_list"))
        self.assertFalse(Category.objects.filter(pk=self.tanks.pk).exists())
        msgs = [str(m) for m in response.context["messages"]]
        self.assertIn("Category 'Tanks' was deleted.", msgs)

    def test_delete_category_with_products_is_blocked_not_500(self):
        """Product.category is PROTECTed; the view must explain, not crash."""
        response = self.client.post(
            reverse("core:category_delete", args=[self.pipes.pk]), follow=True
        )
        self.assertRedirects(response, reverse("core:category_list"))
        self.assertTrue(Category.objects.filter(pk=self.pipes.pk).exists())
        msgs = [str(m) for m in response.context["messages"]]
        self.assertIn(
            "Cannot delete 'Pipes' — 1 product still belong to it. "
            "Reassign or remove them first.",
            msgs,
        )

    def test_delete_rejects_get(self):
        response = self.client.get(reverse("core:category_delete", args=[self.tanks.pk]))
        self.assertEqual(response.status_code, 405)
        self.assertTrue(Category.objects.filter(pk=self.tanks.pk).exists())

    def test_delete_missing_category_404s(self):
        self.assertEqual(
            self.client.post(reverse("core:category_delete", args=[9999])).status_code, 404
        )


class DashboardStatsTests(UserFactoryMixin, TestCase):
    """Figures below are hand-computed, so the view is checked against known
    answers rather than against a re-implementation of its own query."""

    @classmethod
    def setUpTestData(cls):
        cls.today = timezone.localdate()
        cat = Category.objects.create(name="Pipes")
        cls.product = Product.objects.create(
            name="Pipe", size="50mm", category=cat, qty=100, default_price=100
        )

        # Balances: debtors are negative. Owed = 5000 + 12000 + 800 = 17800.
        # Sithara (+2000) is in credit and must be excluded entirely.
        cls.debtor_big = Customer.objects.create(name="Big Debtor", balance=Decimal("-12000.00"))
        cls.debtor_mid = Customer.objects.create(name="Mid Debtor", balance=Decimal("-5000.00"))
        cls.debtor_small = Customer.objects.create(name="Small Debtor", balance=Decimal("-800.00"))
        cls.settled = Customer.objects.create(name="Settled", balance=Decimal("0.00"))
        cls.in_credit = Customer.objects.create(name="In Credit", balance=Decimal("2000.00"))

        # Today's sales: 1000 + 2500 = 3500. Cancelled 9999 excluded;
        # yesterday's 7777 excluded.
        Bill.objects.create(
            customer=cls.debtor_big, bill_date=cls.today,
            total_amount=Decimal("1000.00"), payment_type=Bill.PaymentType.FULL_CASH,
            status=Bill.Status.PAID,
        )
        Bill.objects.create(
            customer=cls.debtor_mid, bill_date=cls.today,
            total_amount=Decimal("2500.00"), payment_type=Bill.PaymentType.PARTIAL,
            status=Bill.Status.PARTIAL,
        )
        Bill.objects.create(
            customer=cls.debtor_mid, bill_date=cls.today,
            total_amount=Decimal("9999.00"), payment_type=Bill.PaymentType.PAY_LATER,
            status=Bill.Status.CANCELLED,
        )
        cls.old_bill = Bill.objects.create(
            customer=cls.debtor_small, bill_date=cls.today - timedelta(days=1),
            total_amount=Decimal("7777.00"), payment_type=Bill.PaymentType.PAY_LATER,
            status=Bill.Status.UNPAID,
        )

        # Cash drawer: 5000 in - 1200 out - 800 transfer = 3000.
        CashDrawer.objects.create(txn_date=cls.today, txn_type=CashDrawer.TxnType.IN, amount=Decimal("5000.00"))
        CashDrawer.objects.create(txn_date=cls.today, txn_type=CashDrawer.TxnType.OUT, amount=Decimal("1200.00"))
        CashDrawer.objects.create(txn_date=cls.today, txn_type=CashDrawer.TxnType.TRANSFER, amount=Decimal("800.00"))

        payment = Payment.objects.create(
            bill=cls.old_bill, method=Payment.Method.CHEQUE,
            amount=Decimal("100.00"), paid_at=timezone.now(),
        )

        def cheque(no, days, status=Cheque.Status.PENDING, amount="500.00"):
            return Cheque.objects.create(
                payment=payment, customer=cls.debtor_big, cheque_no=no,
                bank_name="BOC", amount=Decimal(amount),
                received_date=cls.today, maturity_date=cls.today + timedelta(days=days),
                status=status,
            )

        # In window (pending/held, 0..3 days): 3 cheques.
        cheque("DUE-TODAY", 0)
        cheque("DUE-3", 3)
        cheque("HELD-2", 2, status=Cheque.Status.HELD)
        # Out of window: day 4, already deposited, bounced, and overdue.
        cheque("DAY-4", 4)
        cheque("DEPOSITED", 1, status=Cheque.Status.DEPOSITED)
        cheque("BOUNCED", 1, status=Cheque.Status.BOUNCED)
        cheque("OVERDUE", -2)

    def setUp(self):
        self.client.force_login(self.make_manager())
        self.response = self.client.get(reverse("core:dashboard"))
        self.ctx = self.response.context

    def test_page_renders(self):
        self.assertEqual(self.response.status_code, 200)

    def test_total_outstanding_sums_negative_balances_as_positive(self):
        self.assertEqual(self.ctx["total_outstanding"], Decimal("17800.00"))

    def test_todays_sales_excludes_cancelled_and_other_days(self):
        self.assertEqual(self.ctx["todays_sales"], Decimal("3500.00"))

    def test_cash_drawer_balance_nets_in_out_and_transfer(self):
        self.assertEqual(self.ctx["cash_balance"], Decimal("3000.00"))

    def test_maturing_cheque_count_respects_window_and_status(self):
        self.assertEqual(self.ctx["maturing_count"], 3)
        numbers = {c.cheque_no for c in self.ctx["maturing_cheques"]}
        self.assertEqual(numbers, {"DUE-TODAY", "DUE-3", "HELD-2"})

    def test_maturing_cheques_sorted_by_maturity(self):
        dates = [c.maturity_date for c in self.ctx["maturing_cheques"]]
        self.assertEqual(dates, sorted(dates))

    def test_recent_bills_capped_at_five_newest_first(self):
        bills = list(self.ctx["recent_bills"])
        self.assertLessEqual(len(bills), 5)
        dates = [b.bill_date for b in bills]
        self.assertEqual(dates, sorted(dates, reverse=True))

    def test_top_customers_ordered_by_amount_owed_descending(self):
        names = [c.name for c in self.ctx["top_customers"]]
        self.assertEqual(names, ["Big Debtor", "Mid Debtor", "Small Debtor"])

    def test_top_customers_excludes_settled_and_credit_accounts(self):
        names = [c.name for c in self.ctx["top_customers"]]
        self.assertNotIn("Settled", names)
        self.assertNotIn("In Credit", names)

    def test_owed_annotation_is_positive(self):
        self.assertEqual(self.ctx["top_customers"][0].owed, Decimal("12000.00"))

    def test_cheque_warning_cards_rendered(self):
        self.assertContains(self.response, "DUE-TODAY")
        self.assertContains(self.response, "HELD-2")
        self.assertNotContains(self.response, "DAY-4")

    def test_amounts_rendered_in_page(self):
        self.assertContains(self.response, "17,800.00")  # outstanding
        self.assertContains(self.response, "3,500.00")   # today's sales
        self.assertContains(self.response, "3,000.00")   # cash drawer


class DashboardEmptyStateTests(UserFactoryMixin, TestCase):
    """A brand-new install has no data; aggregates must be 0, not None."""

    def setUp(self):
        self.client.force_login(self.make_manager())
        self.response = self.client.get(reverse("core:dashboard"))

    def test_renders_without_data(self):
        self.assertEqual(self.response.status_code, 200)

    def test_aggregates_are_zero_not_none(self):
        ctx = self.response.context
        self.assertEqual(ctx["total_outstanding"], Decimal("0"))
        self.assertEqual(ctx["todays_sales"], Decimal("0"))
        self.assertEqual(ctx["cash_balance"], Decimal("0"))
        self.assertEqual(ctx["maturing_count"], 0)

    def test_empty_states_shown(self):
        self.assertContains(self.response, "No bills yet.")
        self.assertContains(self.response, "No outstanding balances.")


class CustomerPricePageTests(UserFactoryMixin, TestCase):
    """The two price tables: all customers for a product, all products for a
    customer. Both read the same CustomerPrice rows from opposite sides."""

    @classmethod
    def setUpTestData(cls):
        cat = Category.objects.create(name="Pipes")
        cls.pipe = Product.objects.create(
            name="Pipe", size="50mm", category=cat, default_price=Decimal("100.00")
        )
        cls.tank = Product.objects.create(
            name="Tank", size="1000L", category=cat, default_price=Decimal("500.00")
        )
        cls.retired = Product.objects.create(
            name="Retired", category=cat, default_price=Decimal("10.00"), is_active=False
        )

        cls.nimal = Customer.objects.create(name="Nimal Stores")
        cls.kamal = Customer.objects.create(name="Kamal Traders")
        cls.dormant = Customer.objects.create(name="Dormant Co", is_active=False)
        cls.supplier = Customer.objects.create(name="Raw Supplies", is_supplier=True)

        # Nimal alone has a negotiated price on the pipe.
        CustomerPrice.objects.create(
            customer=cls.nimal, product=cls.pipe, unit_price=Decimal("85.50")
        )

    def setUp(self):
        self.client.force_login(self.make_manager())

    # ---- product side ----
    def test_product_prices_page_renders_a_row_per_customer(self):
        response = self.client.get(reverse("core:product_prices", args=[self.pipe.pk]))
        self.assertEqual(response.status_code, 200)
        names = [r["customer"].name for r in response.context["rows"]]
        self.assertEqual(names, ["Kamal Traders", "Nimal Stores"])
        self.assertEqual(response.context["custom_count"], 1)

    def test_product_prices_shows_custom_price_and_default_fallback(self):
        response = self.client.get(reverse("core:product_prices", args=[self.pipe.pk]))
        rows = {r["customer"].name: r for r in response.context["rows"]}
        self.assertTrue(rows["Nimal Stores"]["has_custom"])
        self.assertEqual(rows["Nimal Stores"]["unit_price"], Decimal("85.50"))
        self.assertFalse(rows["Kamal Traders"]["has_custom"])
        self.assertIsNone(rows["Kamal Traders"]["unit_price"])
        # Kamal has no row of their own, so the product default is shown.
        self.assertContains(response, "default (100.00)")

    def test_product_prices_hides_inactive_customers_and_suppliers(self):
        response = self.client.get(reverse("core:product_prices", args=[self.pipe.pk]))
        names = [r["customer"].name for r in response.context["rows"]]
        self.assertNotIn("Dormant Co", names)
        self.assertNotIn("Raw Supplies", names)

    def test_product_prices_keeps_supplier_that_already_has_a_price(self):
        """Filtering the row away would hide the data it holds."""
        CustomerPrice.objects.create(
            customer=self.supplier, product=self.tank, unit_price=Decimal("400.00")
        )
        response = self.client.get(reverse("core:product_prices", args=[self.tank.pk]))
        names = [r["customer"].name for r in response.context["rows"]]
        self.assertIn("Raw Supplies", names)

    def test_product_prices_missing_product_404s(self):
        response = self.client.get(reverse("core:product_prices", args=[9999]))
        self.assertEqual(response.status_code, 404)

    # ---- customer side ----
    def test_customer_prices_page_renders_a_row_per_product(self):
        response = self.client.get(reverse("core:customer_prices", args=[self.nimal.pk]))
        self.assertEqual(response.status_code, 200)
        names = [r["product"].name for r in response.context["rows"]]
        self.assertEqual(names, ["Pipe", "Tank"])
        self.assertEqual(response.context["custom_count"], 1)

    def test_customer_prices_shows_custom_price_and_default_fallback(self):
        response = self.client.get(reverse("core:customer_prices", args=[self.nimal.pk]))
        rows = {r["product"].name: r for r in response.context["rows"]}
        self.assertTrue(rows["Pipe"]["has_custom"])
        self.assertEqual(rows["Pipe"]["unit_price"], Decimal("85.50"))
        self.assertFalse(rows["Tank"]["has_custom"])
        self.assertContains(response, "default (500.00)")

    def test_customer_prices_hides_inactive_products(self):
        response = self.client.get(reverse("core:customer_prices", args=[self.nimal.pk]))
        names = [r["product"].name for r in response.context["rows"]]
        self.assertNotIn("Retired", names)

    def test_customer_prices_keeps_inactive_product_that_already_has_a_price(self):
        CustomerPrice.objects.create(
            customer=self.kamal, product=self.retired, unit_price=Decimal("9.00")
        )
        response = self.client.get(reverse("core:customer_prices", args=[self.kamal.pk]))
        names = [r["product"].name for r in response.context["rows"]]
        self.assertIn("Retired", names)

    def test_customer_prices_missing_customer_404s(self):
        response = self.client.get(reverse("core:customer_prices", args=[9999]))
        self.assertEqual(response.status_code, 404)

    # ---- both sides agree ----
    def test_both_pages_report_the_same_timestamp(self):
        product_page = self.client.get(reverse("core:product_prices", args=[self.pipe.pk]))
        customer_page = self.client.get(reverse("core:customer_prices", args=[self.nimal.pk]))
        from_product = next(
            r["updated_at"] for r in product_page.context["rows"]
            if r["customer"] == self.nimal
        )
        from_customer = next(
            r["updated_at"] for r in customer_page.context["rows"]
            if r["product"] == self.pipe
        )
        self.assertEqual(from_product, from_customer)
        self.assertNotEqual(from_product, "")

    def test_thousands_render_without_a_separator(self):
        """The price lands in a number input and is compared against the AJAX
        reply, so '1,250.00' would both break the input and fight the reply."""
        CustomerPrice.objects.create(
            customer=self.kamal, product=self.tank, unit_price=Decimal("1250.00")
        )
        response = self.client.get(reverse("core:product_prices", args=[self.tank.pk]))
        self.assertContains(response, "1250.00")
        self.assertNotContains(response, "1,250.00")

    def test_pages_leak_no_template_comments(self):
        """A hash-style comment spanning lines isn't stripped — it renders as
        text on the page. Catch one before it ships."""
        for url in (
            reverse("core:product_prices", args=[self.pipe.pk]),
            reverse("core:customer_prices", args=[self.nimal.pk]),
        ):
            with self.subTest(url=url):
                self.assertNotContains(self.client.get(url), "{#")

    def test_price_pages_require_login(self):
        self.client.logout()
        for url in (
            reverse("core:product_prices", args=[self.pipe.pk]),
            reverse("core:customer_prices", args=[self.nimal.pk]),
        ):
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertEqual(response.status_code, 302)
                self.assertIn(reverse("login"), response["Location"])


class CustomerPriceSaveAllTests(UserFactoryMixin, TestCase):
    """The Save All endpoint behind both price tables."""

    @classmethod
    def setUpTestData(cls):
        cat = Category.objects.create(name="Pipes")
        cls.pipe = Product.objects.create(
            name="Pipe", size="50mm", category=cat, default_price=Decimal("100.00")
        )
        cls.tank = Product.objects.create(
            name="Tank", size="1000L", category=cat, default_price=Decimal("500.00")
        )
        cls.nimal = Customer.objects.create(name="Nimal Stores")
        cls.kamal = Customer.objects.create(name="Kamal Traders")
        cls.url = reverse("core:customer_price_save_all")

    def setUp(self):
        self.user = self.make_manager()
        self.client.force_login(self.user)

    def row(self, customer=None, product=None, unit_price="85.50"):
        return {
            "customer_id": (customer or self.nimal).pk,
            "product_id": (product or self.pipe).pk,
            "unit_price": unit_price,
        }

    def post(self, rows, client=None, **extra):
        return (client or self.client).post(
            self.url,
            json.dumps({"rows": rows}),
            content_type="application/json",
            **extra,
        )

    # ---- the happy path ----
    def test_creates_every_row_in_one_call(self):
        response = self.post(
            [
                self.row(unit_price="85.50"),
                self.row(customer=self.kamal, unit_price="90.00"),
                self.row(product=self.tank, unit_price="450.00"),
            ]
        )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertTrue(body["success"])
        self.assertEqual(body["saved"], 3)
        self.assertEqual(CustomerPrice.objects.count(), 3)

        saved = {
            (p.customer_id, p.product_id): p.unit_price
            for p in CustomerPrice.objects.all()
        }
        self.assertEqual(
            saved,
            {
                (self.nimal.pk, self.pipe.pk): Decimal("85.50"),
                (self.kamal.pk, self.pipe.pk): Decimal("90.00"),
                (self.nimal.pk, self.tank.pk): Decimal("450.00"),
            },
        )

    def test_mixes_creates_and_updates_in_one_batch(self):
        CustomerPrice.objects.create(
            customer=self.nimal, product=self.pipe, unit_price=Decimal("85.50")
        )
        response = self.post(
            [
                self.row(unit_price="72.00"),                      # update
                self.row(customer=self.kamal, unit_price="90.00"),  # create
            ]
        )
        body = response.json()
        self.assertEqual(body["saved"], 2)

        created = {r["created"] for r in body["results"]}
        self.assertEqual(created, {True, False})

        # Updated, not duplicated — (customer, product) is unique_together.
        self.assertEqual(CustomerPrice.objects.count(), 2)
        self.assertEqual(
            CustomerPrice.objects.get(customer=self.nimal).unit_price,
            Decimal("72.00"),
        )

    def test_results_carry_what_each_row_needs_to_repaint(self):
        response = self.post([self.row(unit_price="90")])
        result = response.json()["results"][0]
        # The row prints these verbatim, so 90 must come back as 90.00.
        self.assertEqual(result["price"], "90.00")
        self.assertEqual(result["customer_id"], str(self.nimal.pk))
        self.assertEqual(result["product_id"], str(self.pipe.pk))
        self.assertTrue(result["updated_at"])

    def test_zero_is_a_real_price_not_a_missing_one(self):
        self.assertTrue(self.post([self.row(unit_price="0")]).json()["success"])
        self.assertEqual(CustomerPrice.objects.get().unit_price, Decimal("0.00"))

    def test_bumps_updated_at(self):
        self.post([self.row()])
        first = CustomerPrice.objects.get().updated_at
        self.post([self.row(unit_price="72.00")])
        self.assertGreater(CustomerPrice.objects.get().updated_at, first)

    # ---- all or nothing ----
    def test_one_bad_row_saves_nothing(self):
        """A half-applied batch would leave the operator guessing which half
        landed, so the whole save is refused."""
        response = self.post(
            [
                self.row(unit_price="85.50"),                      # fine
                self.row(customer=self.kamal, unit_price="-1"),     # bad
                self.row(product=self.tank, unit_price="450.00"),   # fine
            ]
        )
        self.assertEqual(response.status_code, 400)
        body = response.json()
        self.assertFalse(body["success"])
        self.assertEqual(body["error"], "Nothing saved — fix 1 row and try again.")
        self.assertFalse(CustomerPrice.objects.exists())

    def test_a_bad_row_does_not_undo_prices_saved_earlier(self):
        self.post([self.row(unit_price="85.50")])
        self.post([self.row(unit_price="-1")])
        self.assertEqual(CustomerPrice.objects.get().unit_price, Decimal("85.50"))

    def test_errors_name_their_row_and_reason(self):
        response = self.post(
            [
                self.row(unit_price="abc"),
                self.row(customer=self.kamal, unit_price="10.005"),
            ]
        )
        self.assertEqual(response.status_code, 400)
        body = response.json()
        self.assertEqual(body["error"], "Nothing saved — fix 2 rows and try again.")

        # Keyed by id so the page can pin each message to its own row.
        reported = {
            (e["customer_id"], e["product_id"]): e["error"] for e in body["errors"]
        }
        self.assertEqual(
            reported,
            {
                (str(self.nimal.pk), str(self.pipe.pk)): "Enter a valid number.",
                (str(self.kamal.pk), str(self.pipe.pk)): "Use at most 2 decimal places.",
            },
        )

    def test_invalid_prices_are_each_rejected_with_their_message(self):
        cases = {
            "-1": "Price cannot be negative.",
            "abc": "Enter a valid number.",
            "": "Enter a price.",
            "10.005": "Use at most 2 decimal places.",
        }
        for value, expected in cases.items():
            with self.subTest(unit_price=value):
                response = self.post([self.row(unit_price=value)])
                self.assertEqual(response.status_code, 400)
                self.assertEqual(response.json()["errors"][0]["error"], expected)
        self.assertFalse(CustomerPrice.objects.exists())

    def test_unknown_customer_or_product_is_rejected(self):
        for field, expected in (
            ("customer_id", "That customer no longer exists."),
            ("product_id", "That product no longer exists."),
        ):
            with self.subTest(field=field):
                row = self.row()
                row[field] = 9999
                response = self.post([row])
                self.assertEqual(response.status_code, 400)
                self.assertEqual(response.json()["errors"][0]["error"], expected)
        self.assertFalse(CustomerPrice.objects.exists())

    # ---- malformed payloads ----
    def test_empty_batch_is_rejected(self):
        response = self.post([])
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"], "No changes to save.")

    def test_oversized_batch_is_refused(self):
        response = self.post([self.row() for _ in range(501)])
        self.assertEqual(response.status_code, 400)
        self.assertIn("Too many rows", response.json()["error"])
        self.assertFalse(CustomerPrice.objects.exists())

    def test_junk_payloads_are_refused_not_500s(self):
        cases = [
            "not json at all",
            json.dumps({"rows": "nope"}),
            json.dumps({"rows": ["nope"]}),
            json.dumps({}),
            json.dumps([]),
            "",
        ]
        for body in cases:
            with self.subTest(body=body[:20]):
                response = self.client.post(
                    self.url, body, content_type="application/json"
                )
                self.assertEqual(response.status_code, 400)
                self.assertFalse(response.json()["success"])
        self.assertFalse(CustomerPrice.objects.exists())

    # ---- access ----
    def test_get_is_rejected(self):
        self.assertEqual(self.client.get(self.url).status_code, 405)

    def test_accepts_the_csrf_header_the_page_sends(self):
        """The default test client skips CSRF, so enforce it here: the token
        the price page bakes into its script has to satisfy a real POST."""
        strict = Client(enforce_csrf_checks=True)
        strict.force_login(self.user)

        # Rendering the token into the script is what plants the cookie. Pull
        # the token back out of the script and send exactly what the page sends
        # — it is masked, so it never equals the cookie value.
        page = strict.get(reverse("core:product_prices", args=[self.pipe.pk]))
        self.assertIn("csrftoken", page.cookies)
        token = re.search(r"const CSRF\s*=\s*'([^']+)'", page.content.decode())
        self.assertIsNotNone(token, "price page did not render a CSRF token")

        response = self.post(
            [self.row()], client=strict, HTTP_X_CSRFTOKEN=token.group(1)
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["success"])

    def test_save_without_a_csrf_token_is_refused(self):
        strict = Client(enforce_csrf_checks=True)
        strict.force_login(self.user)
        self.assertEqual(self.post([self.row()], client=strict).status_code, 403)
        self.assertFalse(CustomerPrice.objects.exists())

    def test_save_requires_login(self):
        self.client.logout()
        response = self.post([self.row()])
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("login"), response["Location"])
        self.assertFalse(CustomerPrice.objects.exists())


class ContextProcessorTests(UserFactoryMixin, TestCase):
    def test_current_role_exposed_for_manager(self):
        self.client.force_login(self.make_manager())
        response = self.client.get(reverse("core:dashboard"))
        self.assertEqual(response.context["current_role"], "manager")
        self.assertFalse(response.context["is_super_admin"])

    def test_current_role_exposed_for_admin(self):
        self.client.force_login(self.make_admin())
        response = self.client.get(reverse("core:dashboard"))
        self.assertEqual(response.context["current_role"], "super_admin")
        self.assertTrue(response.context["is_super_admin"])

    def test_current_role_is_none_for_anonymous(self):
        response = self.client.get(reverse("login"))
        self.assertIsNone(response.context["current_role"])
