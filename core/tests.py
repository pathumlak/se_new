import json
import re
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import connection
from django.test import Client, SimpleTestCase, TestCase
from django.test.utils import CaptureQueriesContext
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
    SupplierBill,
)

User = get_user_model()

# Every sidebar destination, and whether a manager may open it.
NAV_URL_NAMES = [
    ("core:dashboard", True),
    ("core:category_list", False),  # super_admin only
    ("core:product_list", True),
    ("core:customer_list", True),
    ("core:bill_create", True),
    ("core:bill_list", True),
    ("core:cheque_list", True),
    ("core:cash_drawer", True),
    ("core:supplier_bill_list", True),
    ("core:production", True),
    ("core:ledger_index", True),
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
        """Make Bill (/bills/create/) must not also light up Bill List (/bills/)."""
        self.client.force_login(self.make_admin())
        html = self.client.get(reverse("core:bill_create")).content.decode()
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


class TemplateCommentTests(SimpleTestCase):
    """Django strips a {# #} comment only when it opens and closes on one line.
    Spanning lines it is left alone and renders onto the page as text, which is
    silent, easy to miss in review, and has shipped here before. Checked over
    the source rather than per-page, so a comment inside a branch nobody
    happens to render still gets caught."""

    def test_no_hash_comment_spans_lines(self):
        offenders = []
        for path in sorted(Path(settings.BASE_DIR, "templates").rglob("*.html")):
            for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                if "{#" in line and "#}" not in line:
                    offenders.append(f"{path.name}:{number}")

        self.assertEqual(
            offenders,
            [],
            "Use {% comment %} for comments spanning lines: " + ", ".join(offenders),
        )


class CustomerListTests(UserFactoryMixin, TestCase):
    """Available credit is hand-computed below, so the view is checked against
    known answers rather than a re-implementation of its own query."""

    @classmethod
    def setUpTestData(cls):
        def customer(name, limit="0.00", balance="0.00", **kwargs):
            return Customer.objects.create(
                name=name,
                credit_limit=Decimal(limit),
                balance=Decimal(balance),
                **kwargs,
            )

        # limit 10000, owes 4000 -> 6000 left
        cls.debtor = customer("Nimal Stores", "10000.00", "-4000.00", phone="077 123 4567")
        # limit 5000, owes 8000 -> nothing left, never -3000
        cls.over = customer("Over Limit", "5000.00", "-8000.00")
        # in credit: we owe them, which is not extra headroom -> full 2000
        cls.credit = customer("In Credit", "2000.00", "500.00")
        cls.settled = customer("Settled Co", "1000.00", "0.00")
        cls.supplier = customer("Raw Supplies", is_supplier=True)
        cls.dormant = customer("Dormant Co", is_active=False)

    def setUp(self):
        self.client.force_login(self.make_manager())

    def rows(self, **params):
        response = self.client.get(reverse("core:customer_list"), params)
        return {c.name: c for c in response.context["customers"]}

    # ---- available credit ----
    def test_available_credit_is_limit_less_what_is_owed(self):
        self.assertEqual(self.rows()["Nimal Stores"].available_credit, Decimal("6000.00"))

    def test_available_credit_floors_at_zero_when_over_the_limit(self):
        self.assertEqual(self.rows()["Over Limit"].available_credit, Decimal("0.00"))

    def test_credit_in_hand_is_not_extra_headroom(self):
        """A positive balance is money we owe them, not spare credit."""
        self.assertEqual(self.rows()["In Credit"].available_credit, Decimal("2000.00"))

    def test_settled_customer_has_the_whole_limit_available(self):
        self.assertEqual(self.rows()["Settled Co"].available_credit, Decimal("1000.00"))

    def test_owed_is_positive_for_debtors_and_zero_for_everyone_else(self):
        rows = self.rows()
        self.assertEqual(rows["Nimal Stores"].owed, Decimal("4000.00"))
        self.assertEqual(rows["In Credit"].owed, Decimal("0.00"))
        self.assertEqual(rows["Settled Co"].owed, Decimal("0.00"))

    def test_fully_used_limit_renders_as_zero(self):
        response = self.client.get(reverse("core:customer_list"), {"q": "Over Limit"})
        self.assertContains(response, "0.00")

    # ---- search and filters ----
    def test_search_filters_by_name(self):
        self.assertEqual(list(self.rows(q="nimal")), ["Nimal Stores"])

    def test_filter_by_suppliers_only(self):
        self.assertEqual(list(self.rows(kind="suppliers")), ["Raw Supplies"])

    def test_filter_by_customers_only_excludes_suppliers(self):
        self.assertNotIn("Raw Supplies", self.rows(kind="customers"))

    def test_filter_by_status(self):
        self.assertEqual(list(self.rows(status="inactive")), ["Dormant Co"])
        self.assertNotIn("Dormant Co", self.rows(status="active"))

    def test_filters_combine(self):
        rows = self.rows(kind="customers", status="active", q="o")
        # Three active non-suppliers contain an 'o'. 'Dormant Co' does too but
        # is inactive, and the supplier has no 'o' at all — both are excluded
        # by the filters rather than by the search.
        self.assertEqual(set(rows), {"Nimal Stores", "Over Limit", "Settled Co"})

    def test_unknown_filter_values_are_ignored_not_500s(self):
        response = self.client.get(
            reverse("core:customer_list"), {"kind": "zzz", "status": "zzz"}
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.context["customers"]), 6)
        # Nothing was filtered, so the page must not claim it was.
        self.assertFalse(response.context["is_filtered"])

    def test_search_with_no_matches_renders_empty_state(self):
        response = self.client.get(reverse("core:customer_list"), {"q": "zzzz"})
        self.assertEqual(list(response.context["customers"]), [])
        self.assertContains(response, "No customers match")

    # ---- counts are not inflated by the joins ----
    def test_counts_survive_multiple_joins(self):
        """Four joined counts on one row inflate each other without distinct."""
        cat = Category.objects.create(name="Pipes")
        pipe = Product.objects.create(name="Pipe", category=cat, default_price=Decimal("100.00"))
        tank = Product.objects.create(name="Tank", category=cat, default_price=Decimal("500.00"))
        for product in (pipe, tank):
            CustomerPrice.objects.create(
                customer=self.debtor, product=product, unit_price=Decimal("90.00")
            )
        for _ in range(3):
            Bill.objects.create(
                customer=self.debtor,
                bill_date=timezone.localdate(),
                payment_type=Bill.PaymentType.PAY_LATER,
            )

        row = self.rows()["Nimal Stores"]
        self.assertEqual(row.bill_count, 3)
        self.assertEqual(row.custom_price_count, 2)
        self.assertEqual(row.history_count, 3)


class CustomerDetailTests(UserFactoryMixin, TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.customer = Customer.objects.create(
            name="Nimal Stores",
            phone="077 123 4567",
            address="12 Galle Road",
            credit_limit=Decimal("10000.00"),
            balance=Decimal("-4000.00"),
        )

    def setUp(self):
        self.client.force_login(self.make_manager())

    def test_detail_shows_customer_and_credit_info(self):
        response = self.client.get(reverse("core:customer_detail", args=[self.customer.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Nimal Stores")
        self.assertContains(response, "077 123 4567")
        self.assertContains(response, "12 Galle Road")
        self.assertEqual(response.context["customer"].available_credit, Decimal("6000.00"))

    def test_detail_links_to_ledger_and_prices(self):
        response = self.client.get(reverse("core:customer_detail", args=[self.customer.pk]))
        self.assertContains(response, reverse("core:customer_prices", args=[self.customer.pk]))
        self.assertContains(response, reverse("core:customer_ledger", args=[self.customer.pk]))

    def test_detail_missing_customer_404s(self):
        self.assertEqual(
            self.client.get(reverse("core:customer_detail", args=[9999])).status_code, 404
        )

    def test_detail_requires_login(self):
        self.client.logout()
        response = self.client.get(reverse("core:customer_detail", args=[self.customer.pk]))
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("login"), response["Location"])


class CustomerCreditLimitAccessTests(UserFactoryMixin, TestCase):
    """The credit limit is super-admin only, and that has to hold against a
    hand-rolled POST — not just against the template."""

    def base_payload(self, **overrides):
        payload = {
            "name": "Nimal Stores",
            "phone": "",
            "address": "",
            "is_active": "on",
        }
        payload.update(overrides)
        return payload

    # ---- the field itself ----
    def test_manager_form_has_no_credit_limit_field(self):
        self.client.force_login(self.make_manager())
        response = self.client.get(reverse("core:customer_create"))
        self.assertNotIn("credit_limit", response.context["form"].fields)
        self.assertNotContains(response, 'name="credit_limit"')

    def test_admin_form_has_a_credit_limit_field(self):
        self.client.force_login(self.make_admin())
        response = self.client.get(reverse("core:customer_create"))
        self.assertIn("credit_limit", response.context["form"].fields)
        self.assertContains(response, 'name="credit_limit"')

    # ---- the POST ----
    def test_manager_posting_a_credit_limit_cannot_set_one(self):
        """Hiding the input isn't enough: the POST must not reach the column."""
        self.client.force_login(self.make_manager())
        self.client.post(
            reverse("core:customer_create"),
            self.base_payload(credit_limit="999999.00"),
        )
        customer = Customer.objects.get(name="Nimal Stores")
        self.assertEqual(customer.credit_limit, Decimal("0.00"))

    def test_manager_posting_a_credit_limit_cannot_change_an_existing_one(self):
        customer = Customer.objects.create(
            name="Nimal Stores", credit_limit=Decimal("5000.00")
        )
        self.client.force_login(self.make_manager())
        response = self.client.post(
            reverse("core:customer_update", args=[customer.pk]),
            self.base_payload(credit_limit="999999.00"),
        )
        self.assertRedirects(response, reverse("core:customer_list"))
        customer.refresh_from_db()
        self.assertEqual(customer.credit_limit, Decimal("5000.00"))

    def test_admin_can_set_the_credit_limit(self):
        self.client.force_login(self.make_admin())
        self.client.post(
            reverse("core:customer_create"),
            self.base_payload(credit_limit="7500.00"),
        )
        self.assertEqual(
            Customer.objects.get(name="Nimal Stores").credit_limit, Decimal("7500.00")
        )

    def test_negative_credit_limit_is_rejected(self):
        self.client.force_login(self.make_admin())
        response = self.client.post(
            reverse("core:customer_create"),
            self.base_payload(credit_limit="-1.00"),
        )
        self.assertEqual(response.status_code, 200)
        self.assertFormError(
            response.context["form"], "credit_limit", "Credit limit cannot be negative."
        )
        self.assertFalse(Customer.objects.exists())

    # ---- balance is system-managed ----
    def test_balance_is_never_accepted_on_create(self):
        self.client.force_login(self.make_admin())
        self.client.post(
            reverse("core:customer_create"),
            self.base_payload(balance="999.00", credit_limit="0.00"),
        )
        self.assertEqual(Customer.objects.get(name="Nimal Stores").balance, Decimal("0.00"))

    def test_balance_is_never_accepted_on_edit(self):
        customer = Customer.objects.create(
            name="Nimal Stores", balance=Decimal("-4000.00")
        )
        self.client.force_login(self.make_admin())
        self.client.post(
            reverse("core:customer_update", args=[customer.pk]),
            self.base_payload(balance="999.00", credit_limit="0.00"),
        )
        customer.refresh_from_db()
        self.assertEqual(customer.balance, Decimal("-4000.00"))


class CustomerCrudTests(UserFactoryMixin, TestCase):
    def setUp(self):
        self.client.force_login(self.make_manager())

    def payload(self, **overrides):
        data = {"name": "Nimal Stores", "phone": "077", "address": "12 Galle Road", "is_active": "on"}
        data.update(overrides)
        return data

    def test_create_saves_and_redirects_with_message(self):
        response = self.client.post(
            reverse("core:customer_create"), self.payload(), follow=True
        )
        self.assertRedirects(response, reverse("core:customer_list"))
        self.assertTrue(Customer.objects.filter(name="Nimal Stores").exists())
        msgs = [str(m) for m in response.context["messages"]]
        self.assertIn("Customer 'Nimal Stores' was created.", msgs)

    def test_create_rejects_blank_name(self):
        response = self.client.post(reverse("core:customer_create"), self.payload(name=""))
        self.assertEqual(response.status_code, 200)
        self.assertFormError(response.context["form"], "name", "This field is required.")

    def test_create_strips_surrounding_whitespace(self):
        self.client.post(reverse("core:customer_create"), self.payload(name="  Spaced  "))
        self.assertTrue(Customer.objects.filter(name="Spaced").exists())

    def test_new_customer_defaults_to_a_zero_balance(self):
        self.client.post(reverse("core:customer_create"), self.payload())
        self.assertEqual(Customer.objects.get().balance, Decimal("0.00"))

    def test_supplier_flag_is_saved(self):
        self.client.post(reverse("core:customer_create"), self.payload(is_supplier="on"))
        self.assertTrue(Customer.objects.get().is_supplier)

    def test_unchecked_is_active_creates_an_inactive_customer(self):
        payload = self.payload()
        payload.pop("is_active")
        self.client.post(reverse("core:customer_create"), payload)
        self.assertFalse(Customer.objects.get().is_active)

    def test_edit_page_is_prefilled_and_shows_the_balance(self):
        customer = Customer.objects.create(name="Nimal Stores", balance=Decimal("-4000.00"))
        response = self.client.get(reverse("core:customer_update", args=[customer.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["form"].initial["name"], "Nimal Stores")
        self.assertTrue(response.context["is_edit"])
        # Read-only info card, not a field.
        self.assertContains(response, "Current balance")
        self.assertNotContains(response, 'name="balance"')

    def test_edit_saves_and_redirects_with_message(self):
        customer = Customer.objects.create(name="Nimal Stores")
        response = self.client.post(
            reverse("core:customer_update", args=[customer.pk]),
            self.payload(name="Nimal Traders"),
            follow=True,
        )
        self.assertRedirects(response, reverse("core:customer_list"))
        customer.refresh_from_db()
        self.assertEqual(customer.name, "Nimal Traders")
        msgs = [str(m) for m in response.context["messages"]]
        self.assertIn("Customer 'Nimal Traders' was updated.", msgs)

    def test_edit_missing_customer_404s(self):
        self.assertEqual(
            self.client.get(reverse("core:customer_update", args=[9999])).status_code, 404
        )


class CustomerDeleteTests(UserFactoryMixin, TestCase):
    def setUp(self):
        self.client.force_login(self.make_admin())
        self.customer = Customer.objects.create(name="Nimal Stores")

    def url(self, customer=None):
        return reverse("core:customer_delete", args=[(customer or self.customer).pk])

    def give_bill(self, customer=None):
        return Bill.objects.create(
            customer=customer or self.customer,
            bill_date=timezone.localdate(),
            payment_type=Bill.PaymentType.PAY_LATER,
        )

    def test_super_admin_deletes_a_customer_with_no_history(self):
        response = self.client.post(self.url(), follow=True)
        self.assertRedirects(response, reverse("core:customer_list"))
        self.assertFalse(Customer.objects.filter(pk=self.customer.pk).exists())
        msgs = [str(m) for m in response.context["messages"]]
        self.assertIn("Customer 'Nimal Stores' was deleted.", msgs)

    def test_customer_with_bills_is_not_deleted(self):
        self.give_bill()
        response = self.client.post(self.url(), follow=True)
        self.assertRedirects(response, reverse("core:customer_list"))
        self.assertTrue(Customer.objects.filter(pk=self.customer.pk).exists())
        msgs = [str(m) for m in response.context["messages"]]
        self.assertIn(
            "Cannot delete 'Nimal Stores' — it still has 1 bill. "
            "Deactivate it instead to hide it from new bills.",
            msgs,
        )

    def test_customer_with_supplier_bills_is_not_deleted(self):
        SupplierBill.objects.create(
            supplier=self.customer, bill_date=timezone.localdate()
        )
        self.client.post(self.url())
        self.assertTrue(Customer.objects.filter(pk=self.customer.pk).exists())

    def test_blocker_message_names_every_kind_of_history(self):
        self.give_bill()
        self.give_bill()
        SupplierBill.objects.create(
            supplier=self.customer, bill_date=timezone.localdate()
        )
        response = self.client.post(self.url(), follow=True)
        msgs = [str(m) for m in response.context["messages"]]
        self.assertIn(
            "Cannot delete 'Nimal Stores' — it still has 2 bills and 1 supplier bill. "
            "Deactivate it instead to hide it from new bills.",
            msgs,
        )

    def test_customer_with_cheques_is_not_deleted(self):
        bill = self.give_bill()
        payment = Payment.objects.create(
            bill=bill,
            method=Payment.Method.CHEQUE,
            amount=Decimal("100.00"),
            paid_at=timezone.now(),
        )
        Cheque.objects.create(
            payment=payment,
            customer=self.customer,
            cheque_no="C-1",
            bank_name="BOC",
            amount=Decimal("100.00"),
            received_date=timezone.localdate(),
            maturity_date=timezone.localdate(),
        )
        self.client.post(self.url())
        self.assertTrue(Customer.objects.filter(pk=self.customer.pk).exists())

    def test_deleting_takes_its_custom_prices_with_it(self):
        """CustomerPrice CASCADEs, so it must not block the delete."""
        cat = Category.objects.create(name="Pipes")
        product = Product.objects.create(
            name="Pipe", category=cat, default_price=Decimal("100.00")
        )
        CustomerPrice.objects.create(
            customer=self.customer, product=product, unit_price=Decimal("90.00")
        )
        self.client.post(self.url())
        self.assertFalse(Customer.objects.filter(pk=self.customer.pk).exists())
        self.assertFalse(CustomerPrice.objects.exists())

    def test_manager_cannot_delete(self):
        self.client.force_login(self.make_manager())
        response = self.client.post(self.url())
        self.assertRedirects(response, reverse("core:dashboard"))
        self.assertTrue(Customer.objects.filter(pk=self.customer.pk).exists())

    def test_delete_rejects_get(self):
        self.assertEqual(self.client.get(self.url()).status_code, 405)
        self.assertTrue(Customer.objects.filter(pk=self.customer.pk).exists())

    def test_delete_missing_customer_404s(self):
        self.assertEqual(self.client.post(reverse("core:customer_delete", args=[9999])).status_code, 404)

    def test_manager_sees_no_delete_control(self):
        self.client.force_login(self.make_manager())
        html = self.client.get(reverse("core:customer_list")).content.decode()
        self.assertNotIn(self.url(), html)

    def test_delete_is_offered_only_when_there_is_no_history(self):
        response = self.client.get(reverse("core:customer_list"))
        self.assertContains(response, self.url())

        self.give_bill()
        response = self.client.get(reverse("core:customer_list"))
        # The row still lists the customer, but without an actionable delete.
        self.assertNotContains(response, f'data-delete-url="{self.url()}"')
        self.assertContains(response, "deactivate instead")


class CustomerLedgerTests(UserFactoryMixin, TestCase):
    """The running balance below is hand-computed, so the view is checked
    against known answers rather than a re-run of its own arithmetic.

    Timeline for Nimal Stores (balance runs positive = owes us):
      1 Jun  Sale       1000            -> 1000
      2 Jun  Purchase    -300 (che/cash) ->  700
      3 Jun  Sale        500            -> 1200
      3 Jun  Cash        -200 (che/cash) -> 1000
      4 Jun  Cheque      -400 (che/cash) ->  600
    """

    @classmethod
    def setUpTestData(cls):
        cls.customer = Customer.objects.create(
            name="Nimal Stores",
            phone="077 123 4567",
            credit_limit=Decimal("10000.00"),
            balance=Decimal("-600.00"),
        )
        cls.other = Customer.objects.create(name="Someone Else")
        cls.empty = Customer.objects.create(name="No Activity")

        def day(n):
            return date(2026, 6, n)

        def bill(n, total, status=Bill.Status.UNPAID, customer=None):
            return Bill.objects.create(
                customer=customer or cls.customer,
                bill_date=day(n),
                total_amount=Decimal(total),
                payment_type=Bill.PaymentType.PAY_LATER,
                status=status,
            )

        cls.june1 = bill(1, "1000.00")
        cls.june3 = bill(3, "500.00")

        SupplierBill.objects.create(
            supplier=cls.customer, bill_date=day(2), total_amount=Decimal("300.00")
        )

        Payment.objects.create(
            bill=cls.june3,
            method=Payment.Method.CASH,
            amount=Decimal("200.00"),
            paid_at=timezone.make_aware(datetime(2026, 6, 3, 14, 30)),
        )
        Payment.objects.create(
            bill=cls.june1,
            method=Payment.Method.CHEQUE,
            amount=Decimal("400.00"),
            paid_at=timezone.make_aware(datetime(2026, 6, 4, 9, 0)),
        )

        # Noise that must stay out: a cancelled sale, its payment, and another
        # customer's activity.
        cancelled = bill(1, "9999.00", status=Bill.Status.CANCELLED)
        Payment.objects.create(
            bill=cancelled,
            method=Payment.Method.CASH,
            amount=Decimal("9999.00"),
            paid_at=timezone.make_aware(datetime(2026, 6, 1, 10, 0)),
        )
        bill(1, "7777.00", customer=cls.other)

    def setUp(self):
        self.client.force_login(self.make_manager())

    def rows(self, **params):
        response = self.client.get(
            reverse("core:customer_ledger", args=[self.customer.pk]), params
        )
        self.assertEqual(response.status_code, 200)
        self.response = response
        return response.context["rows"]

    def shape(self, rows):
        return [
            (r["date"], r["description"], r["sale"], r["credit"], r["balance"])
            for r in rows
        ]

    def test_running_balance_matches_the_hand_computed_timeline(self):
        self.assertEqual(
            self.shape(self.rows()),
            [
                (date(2026, 6, 1), "Sale", Decimal("1000.00"), None, Decimal("1000.00")),
                (date(2026, 6, 2), "Purchase", None, Decimal("300.00"), Decimal("700.00")),
                (date(2026, 6, 3), "Sale", Decimal("500.00"), None, Decimal("1200.00")),
                (date(2026, 6, 3), "Cash received", None, Decimal("200.00"), Decimal("1000.00")),
                (date(2026, 6, 4), "Cheque received", None, Decimal("400.00"), Decimal("600.00")),
            ],
        )

    def test_rows_are_sorted_by_date_ascending(self):
        dates = [r["date"] for r in self.rows()]
        self.assertEqual(dates, sorted(dates))

    def test_a_sale_precedes_money_taken_the_same_day(self):
        """Both fall on 3 Jun; the sale has to land first or the balance dips
        below what was actually owed."""
        june3 = [r for r in self.rows() if r["date"] == date(2026, 6, 3)]
        self.assertEqual([r["description"] for r in june3], ["Sale", "Cash received"])

    def test_cancelled_bills_and_their_payments_are_excluded(self):
        rows = self.rows()
        self.assertNotIn(Decimal("9999.00"), [r["sale"] for r in rows])
        self.assertNotIn(Decimal("9999.00"), [r["credit"] for r in rows])
        self.assertEqual(len(rows), 5)

    def test_another_customers_activity_is_excluded(self):
        self.assertNotIn(Decimal("7777.00"), [r["sale"] for r in self.rows()])

    def test_supplier_bill_notes_land_in_the_description(self):
        SupplierBill.objects.create(
            supplier=self.customer,
            bill_date=date(2026, 6, 5),
            total_amount=Decimal("50.00"),
            notes="  PVC granules  ",
        )
        rows = self.rows()
        self.assertEqual(rows[-1]["description"], "Purchase - PVC granules")

    def test_purchase_without_notes_has_no_dangling_dash(self):
        descriptions = [r["description"] for r in self.rows()]
        self.assertIn("Purchase", descriptions)
        self.assertNotIn("Purchase - ", descriptions)

    def test_transfer_payments_are_described_too(self):
        """Only cash and cheque were specified, but the model allows transfer;
        it must not render as a blank description."""
        Payment.objects.create(
            bill=self.june1,
            method=Payment.Method.TRANSFER,
            amount=Decimal("25.00"),
            paid_at=timezone.make_aware(datetime(2026, 6, 6, 9, 0)),
        )
        self.assertEqual(self.rows()[-1]["description"], "Transfer received")

    # ---- totals ----
    def test_totals_and_closing_balance(self):
        self.rows()
        self.assertEqual(self.response.context["total_sale"], Decimal("1500.00"))
        self.assertEqual(self.response.context["total_credit"], Decimal("900.00"))
        self.assertEqual(self.response.context["closing_balance"], Decimal("600.00"))

    def test_closing_balance_is_zero_when_there_is_nothing_to_show(self):
        response = self.client.get(reverse("core:customer_ledger", args=[self.empty.pk]))
        self.assertEqual(response.context["closing_balance"], Decimal("0"))
        self.assertContains(response, "No ledger activity yet.")

    # ---- date range ----
    def test_from_date_keeps_only_later_rows(self):
        rows = self.rows(from_date="2026-06-03")
        self.assertEqual([r["date"] for r in rows], [date(2026, 6, 3), date(2026, 6, 3), date(2026, 6, 4)])

    def test_to_date_keeps_only_earlier_rows(self):
        rows = self.rows(to_date="2026-06-02")
        self.assertEqual([r["date"] for r in rows], [date(2026, 6, 1), date(2026, 6, 2)])

    def test_both_bounds_are_inclusive(self):
        rows = self.rows(from_date="2026-06-02", to_date="2026-06-03")
        self.assertEqual(len(rows), 3)

    def test_filtered_balance_restarts_at_zero(self):
        """Per spec the run always starts at 0, so a part-range view is not a
        statement of the whole account. The page says so out loud."""
        rows = self.rows(from_date="2026-06-03")
        self.assertEqual(rows[0]["balance"], Decimal("500.00"))  # not 1200
        self.assertContains(self.response, "Running balance starts at 0")

    def test_unparsable_dates_are_ignored_not_500s(self):
        for params in ({"from_date": "nonsense"}, {"to_date": "2026-02-31"}):
            with self.subTest(params=params):
                rows = self.rows(**params)
                self.assertEqual(len(rows), 5)
                self.assertFalse(self.response.context["is_filtered"])

    # ---- page ----
    def test_header_shows_account_and_credit_info(self):
        self.rows()
        self.assertContains(self.response, "Nimal Stores")
        self.assertContains(self.response, "077 123 4567")
        self.assertContains(self.response, "10,000.00")  # credit limit
        self.assertContains(self.response, "9,400.00")   # available: 10000 - 600 owed
        self.assertEqual(self.response.context["customer"].available_credit, Decimal("9400.00"))

    def test_page_offers_a_pdf_export(self):
        self.rows()
        self.assertContains(self.response, "Export PDF")
        self.assertContains(self.response, "window.print()")

    def test_ledger_missing_customer_404s(self):
        self.assertEqual(
            self.client.get(reverse("core:customer_ledger", args=[9999])).status_code, 404
        )

    def test_ledger_requires_login(self):
        self.client.logout()
        response = self.client.get(reverse("core:customer_ledger", args=[self.customer.pk]))
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("login"), response["Location"])

    def test_customer_pages_link_to_the_ledger(self):
        url = reverse("core:customer_ledger", args=[self.customer.pk])
        self.assertContains(self.client.get(reverse("core:customer_list")), url)
        self.assertContains(
            self.client.get(reverse("core:customer_detail", args=[self.customer.pk])), url
        )


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


class BillCreateStepOneTests(UserFactoryMixin, TestCase):
    """Step 1: pick a customer, then pick products at that customer's price."""

    @classmethod
    def setUpTestData(cls):
        cat = Category.objects.create(name="Pipes")

        def product(name, price, qty="10.000", **kwargs):
            return Product.objects.create(
                name=name,
                category=cat,
                default_price=Decimal(price),
                qty=Decimal(qty),
                **kwargs,
            )

        cls.pipe = product("Pipe", "100.00", size="50mm")
        cls.tank = product("Tank", "500.00", size="1000L")
        cls.out_of_stock = product("Sold Out", "50.00", qty="0.000")
        cls.retired = product("Retired", "50.00", is_active=False)

        cls.nimal = Customer.objects.create(name="Nimal", balance=Decimal("-5000.00"))
        cls.kamal = Customer.objects.create(name="Kamal Traders")
        cls.dormant = Customer.objects.create(name="Dormant Co", is_active=False)
        cls.supplier = Customer.objects.create(name="Raw Supplies", is_supplier=True)

        # Nimal alone has negotiated a price, and only on the pipe.
        CustomerPrice.objects.create(
            customer=cls.nimal, product=cls.pipe, unit_price=Decimal("85.50")
        )

    def setUp(self):
        self.client.force_login(self.make_manager())

    def api(self, customer=None):
        return self.client.get(
            reverse("core:bill_products", args=[(customer or self.nimal).pk])
        )

    def by_name(self, response):
        return {p["name"]: p for p in response.json()}

    # ---- the page ----
    def test_page_renders_with_a_customer_dropdown(self):
        response = self.client.get(reverse("core:bill_create"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'id="customer"')

    def test_dropdown_shows_the_name_and_current_balance(self):
        response = self.client.get(reverse("core:bill_create"))
        self.assertContains(response, "Nimal (Balance: -5,000.00)")

    def test_dropdown_excludes_suppliers_and_inactive_customers(self):
        response = self.client.get(reverse("core:bill_create"))
        names = [c.name for c in response.context["customers"]]
        self.assertEqual(names, ["Kamal Traders", "Nimal"])

    def test_page_requires_login(self):
        self.client.logout()
        response = self.client.get(reverse("core:bill_create"))
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("login"), response["Location"])

    # ---- the endpoint ----
    def test_returns_a_bare_array(self):
        response = self.api()
        self.assertEqual(response.status_code, 200)
        self.assertIsInstance(response.json(), list)

    def test_only_active_in_stock_products_are_offered(self):
        names = set(self.by_name(self.api()))
        self.assertEqual(names, {"Pipe", "Tank"})

    def test_custom_price_wins_where_one_exists(self):
        pipe = self.by_name(self.api())["Pipe"]
        self.assertEqual(pipe["unit_price"], "85.50")
        self.assertTrue(pipe["has_custom_price"])

    def test_default_price_is_used_where_none_exists(self):
        tank = self.by_name(self.api())["Tank"]
        self.assertEqual(tank["unit_price"], "500.00")
        self.assertFalse(tank["has_custom_price"])

    def test_another_customer_gets_the_defaults(self):
        """The override belongs to Nimal, not to the pipe."""
        pipe = self.by_name(self.api(self.kamal))["Pipe"]
        self.assertEqual(pipe["unit_price"], "100.00")
        self.assertFalse(pipe["has_custom_price"])

    def test_row_carries_everything_the_table_prints(self):
        pipe = self.by_name(self.api())["Pipe"]
        self.assertEqual(
            pipe,
            {
                "id": self.pipe.pk,
                "name": "Pipe",
                "size": "50mm",
                "qty": "10",
                "unit_price": "85.50",
                "has_custom_price": True,
            },
        )

    def test_stock_is_trimmed_but_keeps_real_fractions(self):
        self.pipe.qty = Decimal("2.500")
        self.pipe.save(update_fields=["qty"])
        self.assertEqual(self.by_name(self.api())["Pipe"]["qty"], "2.5")

    def test_prices_always_carry_two_decimals(self):
        """The table prints these verbatim, so 90 must arrive as 90.00."""
        self.tank.default_price = Decimal("90")
        self.tank.save(update_fields=["default_price"])
        self.assertEqual(self.by_name(self.api())["Tank"]["unit_price"], "90.00")

    def test_unknown_customer_is_a_json_404_not_an_html_one(self):
        response = self.client.get(reverse("core:bill_products", args=[9999]))
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["error"], "That customer no longer exists.")

    def test_endpoint_rejects_post(self):
        self.assertEqual(
            self.client.post(reverse("core:bill_products", args=[self.nimal.pk])).status_code,
            405,
        )

    def test_endpoint_requires_login(self):
        self.client.logout()
        response = self.api()
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("login"), response["Location"])

    def test_endpoint_cost_does_not_grow_with_the_number_of_rows(self):
        """Asserted as a comparison, not an absolute: the count that matters is
        that twenty more products cost no more queries than two."""
        url = reverse("core:bill_products", args=[self.nimal.pk])

        with CaptureQueriesContext(connection) as few:
            self.client.get(url)

        for n in range(20):
            product = Product.objects.create(
                name=f"Bulk {n}",
                category=self.pipe.category,
                default_price=Decimal("10.00"),
                qty=Decimal("5.000"),
            )
            CustomerPrice.objects.create(
                customer=self.nimal, product=product, unit_price=Decimal("9.00")
            )

        with CaptureQueriesContext(connection) as many:
            response = self.client.get(url)

        self.assertEqual(len(response.json()), 22)
        self.assertEqual(len(many.captured_queries), len(few.captured_queries))

    def test_override_lookup_does_not_join_customer_and_product(self):
        """CustomerPrice.Meta.ordering sorts by related names, which drags both
        tables into a join this dict lookup has no use for."""
        with CaptureQueriesContext(connection) as ctx:
            self.api()

        price_queries = [
            q["sql"] for q in ctx.captured_queries if "core_customerprice" in q["sql"]
        ]
        self.assertEqual(len(price_queries), 1)
        self.assertNotIn("JOIN", price_queries[0])


class BillCreateStepTwoTests(UserFactoryMixin, TestCase):
    """Step 2 lives in JavaScript, which these tests cannot run. What they can
    hold still is the contract it binds to: rename a hook here and the page
    breaks silently in the browser."""

    @classmethod
    def setUpTestData(cls):
        cls.nimal = Customer.objects.create(name="Nimal", balance=Decimal("-5000.00"))

    def setUp(self):
        self.client.force_login(self.make_manager())
        self.response = self.client.get(reverse("core:bill_create"))

    def test_dropdown_carries_the_raw_balance_for_the_summary_maths(self):
        """The label is formatted for reading; the dataset must stay a plain
        number, because the summary parses it."""
        self.assertContains(self.response, 'data-balance="-5000.00"')
        self.assertContains(self.response, 'data-name="Nimal"')

    def test_page_exposes_every_hook_the_script_binds_to(self):
        hooks = [
            # step 2 items table
            "bill-rows",
            "bill-empty",
            "subtotal-cell",
            "invalid-note",
            "add-more",
            "start-over",
            # summary panel
            "summary-customer",
            "summary-balance",
            "summary-subtotal",
            "summary-owed-row",
            "summary-owed",
            "summary-credit-row",
            "summary-credit",
            "summary-collect",
            "summary-collect-note",
            "summary-changed",
            # add-products modal
            "add-modal",
            "add-modal-rows",
            "add-modal-search",
            "add-modal-empty",
            "add-modal-close",
            "add-modal-done",
            # step rail
            "rail-1",
            "rail-2",
        ]
        for hook in hooks:
            with self.subTest(hook=hook):
                self.assertContains(self.response, f'id="{hook}"')

    def test_step_two_starts_hidden(self):
        self.assertContains(self.response, 'id="step-2" class="hidden')


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
