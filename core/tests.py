import json
import re
from datetime import date, datetime, timedelta
from decimal import Decimal
from io import StringIO
from pathlib import Path

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core import mail
from django.core.management import call_command
from django.db import connection
from django.template.loader import render_to_string
from django.test import Client, SimpleTestCase, TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from core import views
from core.models import (
    Bill,
    BillEditAudit,
    BillItem,
    CashDrawer,
    CashTransfer,
    Category,
    Cheque,
    Customer,
    CustomerPrice,
    Payment,
    Product,
    ProductionEntry,
    SupplierBill,
    SupplierBillItem,
)

User = get_user_model()

# Every sidebar destination, and whether a manager may open it.
NAV_URL_NAMES = [
    ("core:dashboard", True),
    ("core:user_list", False),  # super_admin only
    ("core:category_list", False),  # super_admin only
    ("core:product_list", True),
    ("core:customer_list", True),
    ("core:bill_create", True),
    ("core:bill_list", True),
    ("core:cheque_list", True),
    ("core:cash_drawer", True),
    ("core:supplier_bill_list", True),
    ("core:production_list", True),
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
        """Pending only, and no lower bound: a cheque that matured days ago and
        still isn't banked is the most urgent of the lot. Held cheques are ones
        we chose not to bank, so they aren't chased here."""
        self.assertEqual(self.ctx["maturing_count"], 3)
        numbers = {c.cheque_no for c in self.ctx["maturing_cheques"]}
        self.assertEqual(numbers, {"DUE-TODAY", "DUE-3", "OVERDUE"})

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
        self.assertContains(self.response, "OVERDUE")
        self.assertNotContains(self.response, "DAY-4")
        self.assertNotContains(self.response, "HELD-2")

    def test_the_warning_card_can_be_dismissed(self):
        self.assertContains(self.response, 'id="dismiss-cheques"')
        # Keyed to the day and the exact cheques, so a dismissal can't bury a
        # warning for ever or hide a new one behind an old dismissal.
        signature = self.response.context["cheque_signature"]
        self.assertTrue(signature.startswith(self.today.isoformat() + ":"))
        ids = sorted(c.pk for c in self.ctx["maturing_cheques"])
        self.assertEqual(
            signature, f"{self.today.isoformat()}:" + ",".join(str(i) for i in ids)
        )

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
        self.user = self.make_manager()
        self.client.force_login(self.user)

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

    # ---- edit notes ----
    def audit(self, bill, day, reason="Wrong qty entered"):
        return BillEditAudit.objects.create(
            bill=bill,
            edit_date=date(2026, 6, day),
            reason=reason,
            created_by=self.user,
        )

    def test_an_edit_note_carries_the_balance_through_untouched(self):
        """The note explains a figure; it must not move one. 4 Jun closes at
        600, so a note that day leaves the ledger closing at 600."""
        self.audit(self.june1, 4, reason="Price correction")
        rows = self.rows()

        note = rows[-1]
        self.assertEqual(
            self.shape([note]),
            [(
                date(2026, 6, 4),
                f"Bill #{self.june1.pk} edited: Price correction",
                None,
                None,
                Decimal("600.00"),
            )],
        )
        self.assertTrue(note["is_note"])
        self.assertEqual(self.response.context["closing_balance"], Decimal("600.00"))

    def test_an_edit_note_is_not_counted_in_the_totals(self):
        self.rows()
        totals = (
            self.response.context["total_sale"],
            self.response.context["total_credit"],
        )
        self.audit(self.june1, 3)
        self.rows()
        self.assertEqual(
            (self.response.context["total_sale"], self.response.context["total_credit"]),
            totals,
        )

    def test_an_edit_note_lands_last_on_its_day(self):
        """It annotates rows already read; sorting it into the middle of the
        day's money would imply it split them."""
        self.audit(self.june3, 3)
        june3 = [r["description"] for r in self.rows() if r["date"] == date(2026, 6, 3)]
        self.assertEqual(june3[-1], f"Bill #{self.june3.pk} edited: Wrong qty entered")

    def test_a_cancelled_bills_edit_notes_are_excluded(self):
        cancelled = Bill.objects.create(
            customer=self.customer,
            bill_date=date(2026, 6, 1),
            total_amount=Decimal("1234.00"),
            payment_type=Bill.PaymentType.PAY_LATER,
            status=Bill.Status.CANCELLED,
        )
        self.audit(cancelled, 4, reason="Should not show")
        self.assertNotIn(
            "Should not show", [r["description"] for r in self.rows()]
        )

    def test_the_note_renders_italic_and_dashed(self):
        self.audit(self.june1, 4, reason="Price correction")
        self.rows()
        self.assertContains(self.response, "Price correction")
        self.assertContains(self.response, "italic")
        self.assertContains(self.response, "—")

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
        # The endpoint returns {"products": [...], "low_stock_threshold": N}
        # since the stock-UI change; the older bare-array shape is no longer
        # sent.
        return {p["name"]: p for p in response.json()["products"]}

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
    def test_returns_products_and_low_stock_threshold(self):
        response = self.api()
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertIsInstance(payload, dict)
        self.assertIn("products", payload)
        self.assertIsInstance(payload["products"], list)
        # The card grid colours amber/red off this threshold — the client
        # doesn't know it any other way.
        self.assertIn("low_stock_threshold", payload)

    def test_only_active_products_are_offered(self):
        # Out-of-stock products *are* offered — as dimmed, unsellable cards
        # (see the endpoint docstring). What is refused is inactive stock,
        # and stock that has been deleted outright.
        names = set(self.by_name(self.api()))
        self.assertIn("Pipe", names)
        self.assertIn("Tank", names)
        # Sold Out is still active; it comes through with qty 0.
        if "Sold Out" in names:
            self.assertTrue(self.by_name(self.api())["Sold Out"]["is_out_of_stock"])

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
        # Fields the card grid, live-stock reflection and category tabs all
        # consume — spelled out so any accidental drop or rename fails here.
        self.assertEqual(pipe["id"], self.pipe.pk)
        self.assertEqual(pipe["name"], "Pipe")
        self.assertEqual(pipe["size"], "50mm")
        self.assertEqual(pipe["qty"], "10")
        self.assertEqual(pipe["qty_number"], 10.0)
        self.assertEqual(pipe["unit_price"], "85.50")
        self.assertTrue(pipe["has_custom_price"])
        self.assertFalse(pipe["is_out_of_stock"])
        # 10 units against a default threshold of 10 → the low badge trips.
        self.assertTrue(pipe["is_low_stock"])

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

        # All 22 new active rows come back, plus the fixture's own set. The
        # exact figure isn't the point — the query cost is.
        self.assertGreaterEqual(len(response.json()["products"]), 22)
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


class BillCreatePaymentTests(UserFactoryMixin, TestCase):
    """Step 3's validation is JavaScript. What Django owns is the contract it
    reads: the payment codes, the account codes, and who is offered the credit
    limit override."""

    @classmethod
    def setUpTestData(cls):
        cls.nimal = Customer.objects.create(
            name="Nimal",
            balance=Decimal("-5000.00"),
            credit_limit=Decimal("10000.00"),
        )

    def page(self, admin=False):
        self.client.force_login(self.make_admin() if admin else self.make_manager())
        return self.client.get(reverse("core:bill_create"))

    def test_every_payment_type_is_offered(self):
        response = self.page()
        for value, _ in Bill.PaymentType.choices:
            with self.subTest(payment_type=value):
                self.assertContains(response, f'name="payment_type" value="{value}"')
                self.assertContains(response, f'id="pay-{value}"')

    def test_payment_values_come_straight_off_the_model(self):
        """The save step stores these verbatim, so a typo here is a bill with
        an unusable payment_type."""
        self.assertEqual(
            [v for v, _ in self.page().context["payment_types"]],
            ["full_cash", "full_cheque", "partial", "mixed", "pay_later"],
        )

    def test_account_choices_come_straight_off_the_model(self):
        self.assertEqual(
            [v for v, _ in self.page().context["account_choices"]],
            ["senovka", "dinusha"],
        )

    def test_transfer_accounts_are_offered_with_a_physical_cash_default(self):
        response = self.page()
        self.assertContains(response, 'id="fullcash-account"')
        self.assertContains(response, "None (physical cash)")
        self.assertContains(response, "Senovka Account")
        self.assertContains(response, "Dinusha Account")

    def test_cheque_fields_appear_for_every_type_that_takes_one(self):
        response = self.page()
        for prefix in ("fullchq", "partchq", "mixchq"):
            for field in ("no", "bank", "branch", "acc", "amount", "received", "maturity"):
                with self.subTest(field=f"{prefix}-{field}"):
                    self.assertContains(response, f'id="{prefix}-{field}"')

    def test_cheque_received_date_defaults_to_today(self):
        response = self.page()
        today = timezone.localdate().isoformat()
        self.assertContains(response, f'id="fullchq-received" value="{today}"')

    def test_customer_carries_the_credit_limit_for_the_check(self):
        self.assertContains(self.page(), 'data-credit-limit="10000.00"')

    def test_super_admin_gets_the_credit_override(self):
        response = self.page(admin=True)
        self.assertContains(response, 'id="credit-override"')
        self.assertContains(response, "Override the credit limit")

    def test_manager_gets_no_override_control_only_an_explanation(self):
        """The script gates on this element's absence, so a manager must not be
        served one at all."""
        response = self.page()
        self.assertNotContains(response, 'id="credit-override"')
        self.assertContains(response, "Only a super admin can approve")

    def test_script_is_told_the_role(self):
        self.assertContains(self.page(admin=True), "IS_SUPER_ADMIN = true")
        self.assertContains(self.page(), "IS_SUPER_ADMIN = false")

    def test_step_three_starts_hidden(self):
        self.assertContains(self.page(), 'id="step-3" class="hidden')


class BillSaveTests(UserFactoryMixin, TestCase):
    """Every figure below is hand-computed.

    Nimal owes 5,000 (balance -5000). A 1,000 bill means 6,000 settles
    everything: 1,000 for the goods and 5,000 for the debt.
    """

    def setUp(self):
        self.user = self.make_manager()
        self.client.force_login(self.user)

        cat = Category.objects.create(name="Pipes")
        self.pipe = Product.objects.create(
            name="Pipe", size="50mm", category=cat,
            default_price=Decimal("1000.00"), qty=Decimal("10.000"),
        )
        self.tank = Product.objects.create(
            name="Tank", category=cat,
            default_price=Decimal("500.00"), qty=Decimal("10.000"),
        )
        self.nimal = Customer.objects.create(
            name="Nimal", balance=Decimal("-5000.00"), credit_limit=Decimal("10000.00")
        )
        self.url = reverse("core:bill_save")

    def cheque(self, amount="6000.00", **overrides):
        data = {
            "cheque_no": "C-1001",
            "bank_name": "BOC",
            "branch": "Galle",
            "acc_no": "123",
            "amount": amount,
            "received_date": "2026-07-16",
            "maturity_date": "2026-08-16",
        }
        data.update(overrides)
        return data

    def payload(self, payment=None, lines=None, customer=None):
        return {
            "customer_id": (customer or self.nimal).pk,
            "lines": lines if lines is not None else [
                {"product_id": self.pipe.pk, "qty": "1", "unit_price": "1000.00"}
            ],
            "payment": payment or {"type": "full_cash", "cash": "6000.00", "account": ""},
        }

    def post(self, payload=None, **kwargs):
        return self.client.post(
            self.url,
            json.dumps(payload if payload is not None else self.payload()),
            content_type="application/json",
            **kwargs,
        )

    # ---- the happy path ----
    def test_full_cash_writes_the_whole_bill(self):
        response = self.post()
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertTrue(body["success"])

        bill = Bill.objects.get()
        self.assertEqual(body["redirect"], reverse("core:bill_detail", args=[bill.pk]))
        self.assertEqual(bill.customer, self.nimal)
        self.assertEqual(bill.subtotal, Decimal("1000.00"))
        self.assertEqual(bill.total_amount, Decimal("1000.00"))
        self.assertEqual(bill.paid_amount, Decimal("6000.00"))
        self.assertEqual(bill.payment_type, Bill.PaymentType.FULL_CASH)
        self.assertEqual(bill.status, Bill.Status.PAID)
        self.assertEqual(bill.bill_date, timezone.localdate())

    def test_line_is_written_with_a_recomputed_total(self):
        self.post(self.payload(lines=[
            {"product_id": self.pipe.pk, "qty": "2", "unit_price": "1000.00"}
        ], payment={"type": "full_cash", "cash": "7000.00", "account": ""}))
        item = BillItem.objects.get()
        self.assertEqual(item.qty, Decimal("2.000"))
        self.assertEqual(item.unit_price, Decimal("1000.00"))
        self.assertEqual(item.line_total, Decimal("2000.00"))

    def test_a_line_total_from_the_browser_is_ignored(self):
        """The client could send anything; the server does its own sum."""
        self.post(self.payload(lines=[
            {"product_id": self.pipe.pk, "qty": "2", "unit_price": "1000.00",
             "line_total": "1.00"}
        ], payment={"type": "full_cash", "cash": "7000.00", "account": ""}))
        self.assertEqual(BillItem.objects.get().line_total, Decimal("2000.00"))
        self.assertEqual(Bill.objects.get().subtotal, Decimal("2000.00"))

    def test_stock_is_deducted(self):
        self.post(self.payload(lines=[
            {"product_id": self.pipe.pk, "qty": "3", "unit_price": "1000.00"}
        ], payment={"type": "full_cash", "cash": "8000.00", "account": ""}))
        self.pipe.refresh_from_db()
        self.assertEqual(self.pipe.qty, Decimal("7.000"))

    # ---- the balance ----
    def test_paying_in_full_settles_the_balance(self):
        """-5000 owed, 1000 bill, 6000 paid -> square."""
        self.post()
        self.nimal.refresh_from_db()
        self.assertEqual(self.nimal.balance, Decimal("0.00"))
        self.assertEqual(Bill.objects.get().balance_change, Decimal("5000.00"))

    def test_pay_later_deepens_the_debt(self):
        """A sale on credit must make the balance MORE negative, not less."""
        self.post(self.payload(payment={"type": "pay_later"}))
        self.nimal.refresh_from_db()
        self.assertEqual(self.nimal.balance, Decimal("-6000.00"))

        bill = Bill.objects.get()
        self.assertEqual(bill.paid_amount, Decimal("0.00"))
        self.assertEqual(bill.balance_change, Decimal("-1000.00"))
        self.assertEqual(bill.status, Bill.Status.UNPAID)

    def test_balance_change_is_the_move_the_bill_actually_made(self):
        """new_balance = old_balance + balance_change, for any payment type."""
        for payment, cash in (
            ({"type": "pay_later"}, None),
            ({"type": "full_cash", "cash": "6000.00", "account": ""}, "6000.00"),
        ):
            with self.subTest(payment=payment["type"]):
                Bill.objects.all().delete()
                self.nimal.balance = Decimal("-5000.00")
                self.nimal.save(update_fields=["balance"])

                before = self.nimal.balance
                self.post(self.payload(payment=payment))
                self.nimal.refresh_from_db()
                bill = Bill.objects.get()
                self.assertEqual(self.nimal.balance, before + bill.balance_change)

    def test_a_customer_in_credit_can_buy_on_pay_later(self):
        kamal = Customer.objects.create(name="Kamal", balance=Decimal("2500.00"))
        self.post(self.payload(customer=kamal, payment={"type": "pay_later"}))
        kamal.refresh_from_db()
        # 2500 credit less a 1000 bill leaves 1500 credit.
        self.assertEqual(kamal.balance, Decimal("1500.00"))

    # ---- payment types ----
    def test_full_cash_as_physical_cash_lands_in_the_drawer(self):
        self.post()
        payment = Payment.objects.get()
        self.assertEqual(payment.method, Payment.Method.CASH)
        self.assertEqual(payment.amount, Decimal("6000.00"))
        self.assertEqual(payment.account, "")

        entry = CashDrawer.objects.get()
        self.assertEqual(entry.txn_type, CashDrawer.TxnType.IN)
        self.assertEqual(entry.amount, Decimal("6000.00"))
        self.assertEqual(entry.bill, Bill.objects.get())
        self.assertFalse(CashTransfer.objects.exists())

    def test_full_cash_banked_to_an_account_nets_the_drawer_to_zero(self):
        """Cash in then straight out: writing only the transfer would take the
        drawer down by money it never held."""
        self.post(self.payload(
            payment={"type": "full_cash", "cash": "6000.00", "account": "senovka"}
        ))
        transfer = CashTransfer.objects.get()
        self.assertEqual(transfer.to_account, CashTransfer.Account.SENOVKA)
        self.assertEqual(transfer.amount, Decimal("6000.00"))

        kinds = sorted(CashDrawer.objects.values_list("txn_type", flat=True))
        self.assertEqual(kinds, ["in", "transfer"])
        self.assertEqual(views._cash_drawer_balance(), Decimal("0.00"))

    def test_full_cheque_writes_the_cheque(self):
        self.post(self.payload(
            payment={"type": "full_cheque", "cheque": self.cheque()}
        ))
        payment = Payment.objects.get()
        self.assertEqual(payment.method, Payment.Method.CHEQUE)

        cheque = Cheque.objects.get()
        self.assertEqual(cheque.cheque_no, "C-1001")
        self.assertEqual(cheque.bank_name, "BOC")
        self.assertEqual(cheque.branch, "Galle")
        self.assertEqual(cheque.acc_no, "123")
        self.assertEqual(cheque.amount, Decimal("6000.00"))
        self.assertEqual(cheque.customer, self.nimal)
        self.assertEqual(cheque.status, Cheque.Status.PENDING)
        self.assertFalse(CashDrawer.objects.exists())

    def test_partial_writes_both_legs(self):
        self.post(self.payload(payment={
            "type": "partial",
            "cash": "2000.00",
            "cheque": self.cheque(amount="4000.00"),
        }))
        methods = sorted(Payment.objects.values_list("method", flat=True))
        self.assertEqual(methods, ["cash", "cheque"])
        self.assertEqual(Bill.objects.get().paid_amount, Decimal("6000.00"))
        self.assertEqual(Cheque.objects.get().amount, Decimal("4000.00"))

    def test_mixed_writes_all_three_legs(self):
        self.post(self.payload(payment={
            "type": "mixed",
            "cash": "1000.00",
            "transfer": "2000.00",
            "account": "dinusha",
            "cheque": self.cheque(amount="3000.00"),
        }))
        methods = sorted(Payment.objects.values_list("method", flat=True))
        self.assertEqual(methods, ["cash", "cheque", "transfer"])
        self.assertEqual(Bill.objects.get().paid_amount, Decimal("6000.00"))

        transfer = CashTransfer.objects.get()
        self.assertEqual(transfer.to_account, CashTransfer.Account.DINUSHA)
        self.assertEqual(transfer.amount, Decimal("2000.00"))
        # The cash leg reached the drawer; the bank transfer never did.
        self.assertEqual(views._cash_drawer_balance(), Decimal("1000.00"))

    def test_mixed_cheque_is_optional(self):
        self.post(self.payload(payment={
            "type": "mixed", "cash": "4000.00", "transfer": "2000.00",
            "account": "senovka",
        }))
        self.assertTrue(Bill.objects.exists())
        self.assertFalse(Cheque.objects.exists())

    # ---- custom prices ----
    def test_an_edited_price_becomes_the_customers_price(self):
        self.post(self.payload(lines=[
            {"product_id": self.pipe.pk, "qty": "1", "unit_price": "900.00"}
        ], payment={"type": "full_cash", "cash": "5900.00", "account": ""}))
        price = CustomerPrice.objects.get()
        self.assertEqual(price.customer, self.nimal)
        self.assertEqual(price.product, self.pipe)
        self.assertEqual(price.unit_price, Decimal("900.00"))

    def test_an_existing_custom_price_is_updated_not_duplicated(self):
        CustomerPrice.objects.create(
            customer=self.nimal, product=self.pipe, unit_price=Decimal("950.00")
        )
        self.post(self.payload(lines=[
            {"product_id": self.pipe.pk, "qty": "1", "unit_price": "900.00"}
        ], payment={"type": "full_cash", "cash": "5900.00", "account": ""}))
        self.assertEqual(CustomerPrice.objects.count(), 1)
        self.assertEqual(CustomerPrice.objects.get().unit_price, Decimal("900.00"))

    def test_billing_at_the_default_price_creates_no_custom_price(self):
        """Otherwise every product ever sold picks up a redundant override."""
        self.post()
        self.assertFalse(CustomerPrice.objects.exists())

    def test_billing_at_the_existing_custom_price_leaves_it_alone(self):
        CustomerPrice.objects.create(
            customer=self.nimal, product=self.pipe, unit_price=Decimal("900.00")
        )
        before = CustomerPrice.objects.get().updated_at
        self.post(self.payload(lines=[
            {"product_id": self.pipe.pk, "qty": "1", "unit_price": "900.00"}
        ], payment={"type": "full_cash", "cash": "5900.00", "account": ""}))
        self.assertEqual(CustomerPrice.objects.get().updated_at, before)

    def test_the_browsers_price_changed_flag_is_not_trusted(self):
        """The comparison is against the stored price, not against a claim."""
        self.post(self.payload(lines=[
            {"product_id": self.pipe.pk, "qty": "1", "unit_price": "1000.00",
             "price_changed": True}
        ]))
        self.assertFalse(CustomerPrice.objects.exists())

    # ---- validation and rollback ----
    def test_overselling_stock_saves_the_bill_and_drives_stock_negative(self):
        """Overselling is allowed by design — the shelf can go negative and
        the stock ledger records the shortfall. Paid in full so the credit
        check can't fire: 99 x 1000 = 99,000, plus the 5,000 already owed."""
        pipe_before = self.pipe.qty  # 10
        payload = self.payload(lines=[
            {"product_id": self.pipe.pk, "qty": "99", "unit_price": "1000.00"}
        ], payment={"type": "full_cash", "cash": "104000.00", "account": ""})
        payload["bill_date"] = timezone.localdate().isoformat()
        response = self.post(payload)
        self.assertEqual(response.status_code, 200, response.content)

        self.pipe.refresh_from_db()
        self.assertEqual(self.pipe.qty, pipe_before - Decimal("99.000"))
        self.assertLess(self.pipe.qty, Decimal("0"))
        self.assertEqual(Bill.objects.count(), 1)

    def test_a_short_payment_total_saves_nothing(self):
        response = self.post(self.payload(
            payment={"type": "full_cash", "cash": "5000.00", "account": ""}
        ))
        self.assertEqual(response.status_code, 400)
        self.assertIn("Payment must be at least 6000.00", response.json()["error"])
        self.assertRollbackClean()

    def test_full_cash_over_the_target_is_kept_as_credit(self):
        """Paying ahead is allowed: 6,000 settles the bill and the 5,000 owed,
        so 7,000 leaves 1,000 of credit rather than being refused."""
        response = self.post(self.payload(
            payment={"type": "full_cash", "cash": "7000.00", "account": ""}
        ))
        self.assertEqual(response.status_code, 200)

        bill = Bill.objects.get()
        self.assertEqual(bill.status, Bill.Status.PAID)
        self.assertEqual(bill.paid_amount, Decimal("7000.00"))
        # balance_change = paid - total, which carries the excess to the account.
        self.assertEqual(bill.balance_change, Decimal("6000.00"))

        self.nimal.refresh_from_db()
        self.assertEqual(self.nimal.balance, Decimal("1000.00"))  # we owe them

    def test_full_cheque_over_the_target_is_kept_as_credit(self):
        response = self.post(self.payload(payment={
            "type": "full_cheque",
            "cheques": [self.cheque(amount="7000.00")],
        }))
        self.assertEqual(response.status_code, 200)
        self.nimal.refresh_from_db()
        self.assertEqual(self.nimal.balance, Decimal("1000.00"))

    def test_a_bad_second_line_rolls_back_the_first(self):
        """The first line is written and its stock taken before the second one
        fails, so this only holds if the transaction actually unwinds.

        Overselling no longer trips the reversal (see the stock-ledger
        change) — the failure that lands here has to be one the save path
        still refuses. A duplicated product on the same bill does.
        """
        response = self.post(self.payload(lines=[
            {"product_id": self.pipe.pk, "qty": "1", "unit_price": "1000.00"},
            {"product_id": self.pipe.pk, "qty": "2", "unit_price": "1000.00"},
        ], payment={"type": "full_cash", "cash": "8000.00", "account": ""}))
        self.assertEqual(response.status_code, 400)
        self.assertIn("on the bill twice", response.json()["error"])
        self.assertRollbackClean()
        self.pipe.refresh_from_db()
        self.assertEqual(self.pipe.qty, Decimal("10.000"))

    def test_incomplete_cheque_details_save_nothing(self):
        response = self.post(self.payload(payment={
            "type": "full_cheque", "cheque": self.cheque(cheque_no=""),
        }))
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"], "Cheque number is required.")
        self.assertRollbackClean()

    def test_backdated_maturity_is_refused(self):
        response = self.post(self.payload(payment={
            "type": "full_cheque",
            "cheque": self.cheque(received_date="2026-07-16", maturity_date="2026-07-01"),
        }))
        self.assertEqual(response.status_code, 400)
        self.assertIn("cannot be before the received date", response.json()["error"])

    def test_transfer_without_an_account_is_refused(self):
        response = self.post(self.payload(payment={
            "type": "mixed", "cash": "4000.00", "transfer": "2000.00", "account": "",
        }))
        self.assertEqual(response.status_code, 400)
        self.assertIn("account for the transfer", response.json()["error"])

    def test_the_same_product_twice_is_refused(self):
        response = self.post(self.payload(lines=[
            {"product_id": self.pipe.pk, "qty": "1", "unit_price": "1000.00"},
            {"product_id": self.pipe.pk, "qty": "1", "unit_price": "1000.00"},
        ], payment={"type": "pay_later"}))
        self.assertEqual(response.status_code, 400)
        self.assertIn("on the bill twice", response.json()["error"])

    def test_an_inactive_product_cannot_be_billed(self):
        self.pipe.is_active = False
        self.pipe.save(update_fields=["is_active"])
        response = self.post(self.payload(payment={"type": "pay_later"}))
        self.assertEqual(response.status_code, 400)
        self.assertRollbackClean()

    def test_a_supplier_cannot_be_billed(self):
        supplier = Customer.objects.create(name="Raw Supplies", is_supplier=True)
        response = self.post(self.payload(customer=supplier, payment={"type": "pay_later"}))
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"], "That customer can't be billed.")

    def test_an_empty_bill_is_refused(self):
        response = self.post(self.payload(lines=[], payment={"type": "pay_later"}))
        self.assertEqual(response.status_code, 400)
        self.assertIn("at least one product", response.json()["error"])

    def test_junk_payloads_are_refused_not_500s(self):
        for body in ["not json", json.dumps([]), json.dumps({}), ""]:
            with self.subTest(body=body[:12]):
                response = self.client.post(
                    self.url, body, content_type="application/json"
                )
                self.assertEqual(response.status_code, 400)
                self.assertFalse(response.json()["success"])
        self.assertRollbackClean()

    # ---- the credit limit ----
    def test_pay_later_within_the_limit_is_allowed(self):
        self.post(self.payload(payment={"type": "pay_later"}))
        self.assertTrue(Bill.objects.exists())

    def test_manager_cannot_bill_past_the_credit_limit(self):
        response = self.post(self.payload(lines=[
            {"product_id": self.pipe.pk, "qty": "10", "unit_price": "1000.00"}
        ], payment={"type": "pay_later"}))
        self.assertEqual(response.status_code, 400)
        self.assertIn("super admin has to approve", response.json()["error"])
        self.assertRollbackClean()

    def test_a_manager_forging_the_override_is_still_refused(self):
        """The page hides the checkbox from managers; that is a courtesy, not
        a control. The flag comes from the browser."""
        response = self.post(self.payload(lines=[
            {"product_id": self.pipe.pk, "qty": "10", "unit_price": "1000.00"}
        ], payment={"type": "pay_later", "credit_override": True}))
        self.assertEqual(response.status_code, 400)
        self.assertIn("super admin has to approve", response.json()["error"])
        self.assertRollbackClean()

    def test_super_admin_needs_the_override_to_pass_the_limit(self):
        self.client.force_login(self.make_admin())
        response = self.post(self.payload(lines=[
            {"product_id": self.pipe.pk, "qty": "10", "unit_price": "1000.00"}
        ], payment={"type": "pay_later"}))
        self.assertEqual(response.status_code, 400)
        self.assertIn("needs an override", response.json()["error"])
        self.assertRollbackClean()

    def test_super_admin_with_the_override_may_pass_the_limit(self):
        self.client.force_login(self.make_admin())
        response = self.post(self.payload(lines=[
            {"product_id": self.pipe.pk, "qty": "10", "unit_price": "1000.00"}
        ], payment={"type": "pay_later", "credit_override": True}))
        self.assertEqual(response.status_code, 200)
        self.nimal.refresh_from_db()
        self.assertEqual(self.nimal.balance, Decimal("-15000.00"))

    # ---- access ----
    def test_save_rejects_get(self):
        self.assertEqual(self.client.get(self.url).status_code, 405)

    def test_save_requires_login(self):
        self.client.logout()
        response = self.post()
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("login"), response["Location"])
        self.assertRollbackClean()

    def assertRollbackClean(self):
        """Nothing the save touches may survive a refused bill."""
        self.assertFalse(Bill.objects.exists())
        self.assertFalse(BillItem.objects.exists())
        self.assertFalse(Payment.objects.exists())
        self.assertFalse(Cheque.objects.exists())
        self.assertFalse(CashDrawer.objects.exists())
        self.assertFalse(CashTransfer.objects.exists())
        self.assertFalse(CustomerPrice.objects.exists())
        self.nimal.refresh_from_db()
        self.assertEqual(self.nimal.balance, Decimal("-5000.00"))
        self.pipe.refresh_from_db()
        self.assertEqual(self.pipe.qty, Decimal("10.000"))


class BillDetailTests(UserFactoryMixin, TestCase):
    def setUp(self):
        self.client.force_login(self.make_manager())
        cat = Category.objects.create(name="Pipes")
        self.pipe = Product.objects.create(
            name="Pipe", size="50mm", category=cat,
            default_price=Decimal("1000.00"), qty=Decimal("10.000"),
        )
        self.nimal = Customer.objects.create(
            name="Nimal", balance=Decimal("-5000.00"), credit_limit=Decimal("10000.00")
        )
        self.client.post(
            reverse("core:bill_save"),
            json.dumps({
                "customer_id": self.nimal.pk,
                "lines": [{"product_id": self.pipe.pk, "qty": "2", "unit_price": "1000.00"}],
                "payment": {"type": "full_cheque", "cheque": {
                    "cheque_no": "C-1001", "bank_name": "BOC", "branch": "Galle",
                    "acc_no": "123", "amount": "7000.00",
                    "received_date": "2026-07-16", "maturity_date": "2026-08-16",
                }},
            }),
            content_type="application/json",
        )
        self.bill = Bill.objects.get()

    def url(self):
        return reverse("core:bill_detail", args=[self.bill.pk])

    def test_detail_renders_the_bill(self):
        response = self.client.get(self.url())
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, f"Bill #{self.bill.pk}")
        self.assertContains(response, "Nimal")
        self.assertContains(response, "Full Cheque")
        self.assertContains(response, "Paid")

    def test_detail_shows_the_items_and_subtotal(self):
        response = self.client.get(self.url())
        self.assertContains(response, "Pipe")
        self.assertContains(response, "50mm")
        self.assertContains(response, "2,000.00")

    def test_detail_shows_the_cheque(self):
        response = self.client.get(self.url())
        self.assertContains(response, "C-1001")
        self.assertContains(response, "BOC")
        self.assertContains(response, "Galle")

    def test_detail_reconstructs_the_balance_either_side_of_the_bill(self):
        response = self.client.get(self.url())
        # -5000 before, +5000 from the bill, 0 after.
        self.assertEqual(response.context["balance_before"], Decimal("-5000.00"))
        self.assertEqual(response.context["bill"].balance_change, Decimal("5000.00"))
        self.assertEqual(response.context["bill"].customer.balance, Decimal("0.00"))

    def test_saving_flashes_a_message_onto_the_detail_page(self):
        Bill.objects.all().delete()
        self.nimal.balance = Decimal("-5000.00")
        self.nimal.save(update_fields=["balance"])
        response = self.client.post(
            reverse("core:bill_save"),
            json.dumps({
                "customer_id": self.nimal.pk,
                "lines": [{"product_id": self.pipe.pk, "qty": "1", "unit_price": "1000.00"}],
                "payment": {"type": "pay_later"},
            }),
            content_type="application/json",
        )
        bill = Bill.objects.get()
        page = self.client.get(response.json()["redirect"])
        msgs = [str(m) for m in page.context["messages"]]
        self.assertIn(f"Bill #{bill.pk} for Nimal was saved.", msgs)

    def test_detail_missing_bill_404s(self):
        self.assertEqual(
            self.client.get(reverse("core:bill_detail", args=[9999])).status_code, 404
        )

    def test_detail_requires_login(self):
        self.client.logout()
        response = self.client.get(self.url())
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("login"), response["Location"])


class BillMutationMixin(UserFactoryMixin):
    """A saved bill to edit or delete.

    Nimal owes 5,000. The bill is 2 pipes at 1,000 = 2,000, paid in full with
    7,000 cash (2,000 for the goods, 5,000 clearing the debt), which squares
    the account and leaves 8 pipes on the shelf.
    """

    def build(self):
        cat = Category.objects.create(name="Pipes")
        self.pipe = Product.objects.create(
            name="Pipe", size="50mm", category=cat,
            default_price=Decimal("1000.00"), qty=Decimal("10.000"),
        )
        self.tank = Product.objects.create(
            name="Tank", category=cat,
            default_price=Decimal("500.00"), qty=Decimal("10.000"),
        )
        self.nimal = Customer.objects.create(
            name="Nimal", balance=Decimal("-5000.00"), credit_limit=Decimal("50000.00")
        )

        self.client.force_login(self.make_admin())
        self.client.post(
            reverse("core:bill_save"),
            json.dumps({
                "customer_id": self.nimal.pk,
                "lines": [{"product_id": self.pipe.pk, "qty": "2", "unit_price": "1000.00"}],
                "payment": {"type": "full_cash", "cash": "7000.00", "account": ""},
            }),
            content_type="application/json",
        )
        self.bill = Bill.objects.get()

    def assertOriginalBillUndone(self):
        """The world as it was before the bill: stock back, account owing."""
        self.pipe.refresh_from_db()
        self.assertEqual(self.pipe.qty, Decimal("10.000"))
        self.nimal.refresh_from_db()
        self.assertEqual(self.nimal.balance, Decimal("-5000.00"))


class BillDeleteTests(BillMutationMixin, TestCase):
    def setUp(self):
        self.build()

    def url(self):
        return reverse("core:bill_delete", args=[self.bill.pk])

    def test_the_saved_bill_starts_from_the_expected_state(self):
        """Guards the fixture: the reversal tests mean nothing if this drifts."""
        self.pipe.refresh_from_db()
        self.assertEqual(self.pipe.qty, Decimal("8.000"))
        self.nimal.refresh_from_db()
        self.assertEqual(self.nimal.balance, Decimal("0.00"))
        self.assertEqual(CashDrawer.objects.count(), 1)

    def test_delete_reverses_everything_and_removes_the_bill(self):
        response = self.client.post(self.url(), follow=True)
        self.assertRedirects(response, reverse("core:bill_list"))
        self.assertOriginalBillUndone()

        self.assertFalse(Bill.objects.exists())
        self.assertFalse(BillItem.objects.exists())
        self.assertFalse(Payment.objects.exists())
        self.assertFalse(CashDrawer.objects.exists())

        msgs = [str(m) for m in response.context["messages"]]
        self.assertIn(f"Bill #{self.bill.pk} for Nimal was deleted and reversed.", msgs)

    def test_delete_takes_the_cheque_and_transfer_with_it(self):
        """Both hang off Payment by CASCADE, so deleting payments clears them."""
        Bill.objects.all().delete()
        self.nimal.balance = Decimal("-5000.00")
        self.nimal.save(update_fields=["balance"])
        self.pipe.qty = Decimal("10.000")
        self.pipe.save(update_fields=["qty"])

        self.client.post(
            reverse("core:bill_save"),
            json.dumps({
                "customer_id": self.nimal.pk,
                "lines": [{"product_id": self.pipe.pk, "qty": "2", "unit_price": "1000.00"}],
                "payment": {
                    "type": "mixed", "cash": "2000.00", "transfer": "2000.00",
                    "account": "senovka",
                    "cheque": {
                        "cheque_no": "C-1", "bank_name": "BOC", "branch": "", "acc_no": "",
                        "amount": "3000.00", "received_date": "2026-07-16",
                        "maturity_date": "2026-08-16",
                    },
                },
            }),
            content_type="application/json",
        )
        bill = Bill.objects.get()
        self.assertTrue(Cheque.objects.exists())
        self.assertTrue(CashTransfer.objects.exists())

        self.client.post(reverse("core:bill_delete", args=[bill.pk]))
        self.assertFalse(Cheque.objects.exists())
        self.assertFalse(CashTransfer.objects.exists())
        self.assertOriginalBillUndone()

    def test_cash_drawer_entries_do_not_outlive_the_bill(self):
        """CashDrawer.bill is SET_NULL, so a cascade would leave them behind
        still counting toward the drawer balance."""
        self.assertEqual(views._cash_drawer_balance(), Decimal("7000.00"))
        self.client.post(self.url())
        self.assertFalse(CashDrawer.objects.exists())
        self.assertEqual(views._cash_drawer_balance(), Decimal("0.00"))

    def test_a_pay_later_bill_reverses_its_debt(self):
        Bill.objects.all().delete()
        self.nimal.balance = Decimal("-5000.00")
        self.nimal.save(update_fields=["balance"])
        self.pipe.qty = Decimal("10.000")
        self.pipe.save(update_fields=["qty"])

        self.client.post(
            reverse("core:bill_save"),
            json.dumps({
                "customer_id": self.nimal.pk,
                "lines": [{"product_id": self.pipe.pk, "qty": "2", "unit_price": "1000.00"}],
                "payment": {"type": "pay_later"},
            }),
            content_type="application/json",
        )
        self.nimal.refresh_from_db()
        self.assertEqual(self.nimal.balance, Decimal("-7000.00"))

        self.client.post(reverse("core:bill_delete", args=[Bill.objects.get().pk]))
        self.assertOriginalBillUndone()

    def test_manager_cannot_delete_a_bill(self):
        self.client.force_login(self.make_manager())
        response = self.client.post(self.url())
        self.assertRedirects(response, reverse("core:dashboard"))
        self.assertTrue(Bill.objects.filter(pk=self.bill.pk).exists())

    def test_delete_rejects_get(self):
        self.assertEqual(self.client.get(self.url()).status_code, 405)
        self.assertTrue(Bill.objects.filter(pk=self.bill.pk).exists())

    def test_delete_missing_bill_404s(self):
        self.assertEqual(
            self.client.post(reverse("core:bill_delete", args=[9999])).status_code, 404
        )

    def test_modal_spells_out_what_will_be_reversed(self):
        response = self.client.get(reverse("core:bill_detail", args=[self.bill.pk]))
        reverses = json.loads(response.context["reverses"])
        self.assertIn("1 line of stock returned", reverses)
        self.assertIn("Nimal's balance returns to -5000.00", reverses)
        self.assertIn(
            "1 payment record removed, with any cheque or transfer on them", reverses
        )
        self.assertIn("1 cash drawer entry removed", reverses)

    def test_manager_gets_no_delete_control(self):
        self.client.force_login(self.make_manager())
        for url in (reverse("core:bill_list"), reverse("core:bill_detail", args=[self.bill.pk])):
            with self.subTest(url=url):
                html = self.client.get(url).content.decode()
                self.assertNotIn(self.url(), html)
                self.assertNotIn('id="delete-modal"', html)


class BillEditTests(BillMutationMixin, TestCase):
    def setUp(self):
        self.build()
        # Every test below is about the rewrite itself, so they start on the
        # far side of the reason gate. BillEditReasonGateTests covers the gate.
        self.pass_gate()

    def pass_gate(self, edit_date="2026-07-17", reason="Wrong qty entered"):
        session = self.client.session
        session[f"bill_edit_gate:{self.bill.pk}"] = {
            "edit_date": edit_date,
            "reason": reason,
        }
        session.save()

    def url(self):
        return reverse("core:bill_edit", args=[self.bill.pk])

    def post(self, payload):
        return self.client.post(
            self.url(), json.dumps(payload), content_type="application/json"
        )

    # ---- the page ----
    def test_edit_page_renders_the_form(self):
        response = self.client.get(self.url())
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, f"Editing")
        self.assertContains(response, 'id="bill-initial"')
        self.assertContains(response, "Save Changes")

    def test_edit_page_prices_against_the_balance_without_this_bill(self):
        """The bill squared the account, but the form has to read as it did
        before it existed, or the biller re-pays a debt that is back."""
        response = self.client.get(self.url())
        nimal = next(c for c in response.context["customers"] if c.pk == self.nimal.pk)
        self.assertEqual(nimal.balance, Decimal("0.00"))          # stored
        self.assertEqual(nimal.balance_for_bill, Decimal("-5000.00"))  # for pricing
        self.assertContains(response, 'data-balance="-5000.00"')

    def test_other_customers_keep_their_real_balance(self):
        kamal = Customer.objects.create(name="Kamal", balance=Decimal("-800.00"))
        response = self.client.get(self.url())
        row = next(c for c in response.context["customers"] if c.pk == kamal.pk)
        self.assertEqual(row.balance_for_bill, Decimal("-800.00"))

    def test_initial_carries_the_lines_and_payment(self):
        initial = self.client.get(self.url()).context["initial"]
        self.assertEqual(initial["customer_id"], self.nimal.pk)
        self.assertEqual(initial["lines"], [
            {"product_id": self.pipe.pk, "qty": "2", "unit_price": "1000.00"}
        ])
        self.assertEqual(initial["payment"]["type"], "full_cash")
        self.assertEqual(initial["payment"]["cash"], "7000.00")

    def test_initial_carries_cheque_details_back_into_the_form(self):
        Bill.objects.all().delete()
        self.nimal.balance = Decimal("-5000.00")
        self.nimal.save(update_fields=["balance"])
        self.pipe.qty = Decimal("10.000")
        self.pipe.save(update_fields=["qty"])
        self.client.post(
            reverse("core:bill_save"),
            json.dumps({
                "customer_id": self.nimal.pk,
                "lines": [{"product_id": self.pipe.pk, "qty": "2", "unit_price": "1000.00"}],
                "payment": {"type": "full_cheque", "cheque": {
                    "cheque_no": "C-9", "bank_name": "HNB", "branch": "Galle",
                    "acc_no": "77", "amount": "7000.00",
                    "received_date": "2026-07-16", "maturity_date": "2026-08-16",
                }},
            }),
            content_type="application/json",
        )
        bill = Bill.objects.get()
        initial = self.client.get(reverse("core:bill_edit", args=[bill.pk])).context["initial"]
        self.assertEqual(initial["payment"]["cheque"]["cheque_no"], "C-9")
        self.assertEqual(initial["payment"]["cheque"]["bank_name"], "HNB")
        self.assertEqual(initial["payment"]["cheque"]["received_date"], "2026-07-16")

    def test_products_endpoint_hands_back_this_bills_own_stock(self):
        """8 on the shelf, 2 held by this bill: the edit may use all 10."""
        plain = self.client.get(
            reverse("core:bill_products", args=[self.nimal.pk])
        ).json()["products"]
        self.assertEqual(next(p for p in plain if p["id"] == self.pipe.pk)["qty"], "8")

        editing = self.client.get(
            reverse("core:bill_products", args=[self.nimal.pk]),
            {"bill": self.bill.pk},
        ).json()["products"]
        self.assertEqual(next(p for p in editing if p["id"] == self.pipe.pk)["qty"], "10")

    def test_a_product_this_bill_cleared_out_is_still_offered(self):
        """Otherwise a bill that took the last unit could never be edited."""
        self.pipe.qty = Decimal("0.000")
        self.pipe.save(update_fields=["qty"])

        plain = self.client.get(
            reverse("core:bill_products", args=[self.nimal.pk])
        ).json()["products"]
        self.assertNotIn(self.pipe.pk, [p["id"] for p in plain])

        editing = self.client.get(
            reverse("core:bill_products", args=[self.nimal.pk]), {"bill": self.bill.pk}
        ).json()["products"]
        self.assertEqual(next(p for p in editing if p["id"] == self.pipe.pk)["qty"], "2")

    # ---- rewriting ----
    def test_resaving_an_unchanged_bill_changes_nothing(self):
        """The reversal and the re-apply have to cancel exactly, or every open
        and save quietly doubles the bill."""
        response = self.post({
            "customer_id": self.nimal.pk,
            "lines": [{"product_id": self.pipe.pk, "qty": "2", "unit_price": "1000.00"}],
            "payment": {"type": "full_cash", "cash": "7000.00", "account": ""},
        })
        self.assertEqual(response.status_code, 200)

        self.pipe.refresh_from_db()
        self.assertEqual(self.pipe.qty, Decimal("8.000"))
        self.nimal.refresh_from_db()
        self.assertEqual(self.nimal.balance, Decimal("0.00"))
        self.assertEqual(Bill.objects.count(), 1)
        self.assertEqual(BillItem.objects.count(), 1)
        self.assertEqual(Payment.objects.count(), 1)
        self.assertEqual(CashDrawer.objects.count(), 1)

    def test_editing_the_quantity_moves_stock_by_the_difference(self):
        # 2 pipes -> 3. Bill 3,000; with the 5,000 debt back, 8,000 settles it.
        self.post({
            "customer_id": self.nimal.pk,
            "lines": [{"product_id": self.pipe.pk, "qty": "3", "unit_price": "1000.00"}],
            "payment": {"type": "full_cash", "cash": "8000.00", "account": ""},
        })
        self.pipe.refresh_from_db()
        self.assertEqual(self.pipe.qty, Decimal("7.000"))

        bill = Bill.objects.get()
        self.assertEqual(bill.pk, self.bill.pk)  # same bill, rewritten
        self.assertEqual(bill.subtotal, Decimal("3000.00"))
        self.assertEqual(BillItem.objects.get().qty, Decimal("3.000"))

    def test_editing_keeps_the_original_bill_date(self):
        self.bill.bill_date = date(2026, 1, 5)
        self.bill.save(update_fields=["bill_date"])
        self.post({
            "customer_id": self.nimal.pk,
            "lines": [{"product_id": self.pipe.pk, "qty": "2", "unit_price": "1000.00"}],
            "payment": {"type": "full_cash", "cash": "7000.00", "account": ""},
        })
        self.assertEqual(Bill.objects.get().bill_date, date(2026, 1, 5))

    def test_swapping_the_product_returns_the_old_stock_and_takes_the_new(self):
        self.post({
            "customer_id": self.nimal.pk,
            "lines": [{"product_id": self.tank.pk, "qty": "4", "unit_price": "500.00"}],
            "payment": {"type": "full_cash", "cash": "7000.00", "account": ""},
        })
        self.pipe.refresh_from_db()
        self.tank.refresh_from_db()
        self.assertEqual(self.pipe.qty, Decimal("10.000"))  # all returned
        self.assertEqual(self.tank.qty, Decimal("6.000"))

    def test_changing_the_payment_type_rewrites_the_money(self):
        self.post({
            "customer_id": self.nimal.pk,
            "lines": [{"product_id": self.pipe.pk, "qty": "2", "unit_price": "1000.00"}],
            "payment": {"type": "pay_later"},
        })
        bill = Bill.objects.get()
        self.assertEqual(bill.payment_type, Bill.PaymentType.PAY_LATER)
        self.assertEqual(bill.paid_amount, Decimal("0.00"))
        self.assertEqual(bill.status, Bill.Status.UNPAID)
        self.assertFalse(Payment.objects.exists())
        self.assertFalse(CashDrawer.objects.exists())

        # 5,000 owed before, plus a 2,000 bill paid for by nobody.
        self.nimal.refresh_from_db()
        self.assertEqual(self.nimal.balance, Decimal("-7000.00"))

    def test_moving_the_bill_to_another_customer_squares_both(self):
        kamal = Customer.objects.create(
            name="Kamal", balance=Decimal("0.00"), credit_limit=Decimal("50000.00")
        )
        self.post({
            "customer_id": kamal.pk,
            "lines": [{"product_id": self.pipe.pk, "qty": "2", "unit_price": "1000.00"}],
            "payment": {"type": "pay_later"},
        })
        # Nimal gets his debt back and nothing else; Kamal takes the bill.
        self.nimal.refresh_from_db()
        self.assertEqual(self.nimal.balance, Decimal("-5000.00"))
        kamal.refresh_from_db()
        self.assertEqual(kamal.balance, Decimal("-2000.00"))
        self.assertEqual(Bill.objects.get().customer, kamal)

    def test_an_edited_price_becomes_the_customers_price(self):
        self.post({
            "customer_id": self.nimal.pk,
            "lines": [{"product_id": self.pipe.pk, "qty": "2", "unit_price": "900.00"}],
            "payment": {"type": "full_cash", "cash": "6800.00", "account": ""},
        })
        self.assertEqual(CustomerPrice.objects.get().unit_price, Decimal("900.00"))

    # ---- rollback ----
    def test_a_refused_edit_leaves_the_original_bill_intact(self):
        """The reversal runs before validation can fail, so this only holds if
        the whole thing unwinds."""
        response = self.post({
            "customer_id": self.nimal.pk,
            "lines": [{"product_id": self.pipe.pk, "qty": "2", "unit_price": "1000.00"}],
            "payment": {"type": "full_cash", "cash": "1.00", "account": ""},
        })
        self.assertEqual(response.status_code, 400)
        self.assertIn("Payment must be at least 7000.00", response.json()["error"])

        # Everything exactly as the save left it.
        self.pipe.refresh_from_db()
        self.assertEqual(self.pipe.qty, Decimal("8.000"))
        self.nimal.refresh_from_db()
        self.assertEqual(self.nimal.balance, Decimal("0.00"))
        self.assertEqual(Bill.objects.count(), 1)
        self.assertEqual(BillItem.objects.count(), 1)
        self.assertEqual(Payment.objects.count(), 1)
        self.assertEqual(CashDrawer.objects.count(), 1)

        bill = Bill.objects.get()
        self.assertEqual(bill.subtotal, Decimal("2000.00"))
        self.assertEqual(bill.paid_amount, Decimal("7000.00"))

    def test_an_edit_that_oversells_goes_through_and_drives_stock_negative(self):
        """Editing to oversell mirrors the create-time behavior — allowed,
        and the shortfall is what the stock ledger records."""
        # The bill under edit already holds 2 units of pipe, so 99 new units
        # would move stock from 8 (on-shelf) + 2 (this bill's) = 10 to −89.
        response = self.post({
            "customer_id": self.nimal.pk,
            "lines": [{"product_id": self.pipe.pk, "qty": "99", "unit_price": "1000.00"}],
            "payment": {"type": "full_cash", "cash": "104000.00", "account": ""},
        })
        self.assertEqual(response.status_code, 200, response.content)
        self.pipe.refresh_from_db()
        self.assertEqual(self.pipe.qty, Decimal("-89.000"))
        self.assertEqual(Bill.objects.count(), 1)

    def test_edit_flashes_a_message_onto_the_detail_page(self):
        response = self.post({
            "customer_id": self.nimal.pk,
            "lines": [{"product_id": self.pipe.pk, "qty": "2", "unit_price": "1000.00"}],
            "payment": {"type": "full_cash", "cash": "7000.00", "account": ""},
        })
        page = self.client.get(response.json()["redirect"])
        msgs = [str(m) for m in page.context["messages"]]
        self.assertIn(f"Bill #{self.bill.pk} was updated.", msgs)

    def test_a_manager_may_edit(self):
        self.client.force_login(self.make_manager())
        response = self.client.get(self.url())
        self.assertEqual(response.status_code, 200)

    def test_edit_requires_login(self):
        self.client.logout()
        response = self.client.get(self.url())
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("login"), response["Location"])

    def test_edit_missing_bill_404s(self):
        self.assertEqual(
            self.client.get(reverse("core:bill_edit", args=[9999])).status_code, 404
        )

    # ---- the audit trail ----
    def test_a_saved_edit_records_its_date_and_reason(self):
        self.pass_gate(edit_date="2026-07-17", reason="Price correction")
        self.post({
            "customer_id": self.nimal.pk,
            "lines": [{"product_id": self.pipe.pk, "qty": "2", "unit_price": "1000.00"}],
            "payment": {"type": "full_cash", "cash": "7000.00", "account": ""},
        })

        self.bill.refresh_from_db()
        self.assertEqual(self.bill.edit_date, date(2026, 7, 17))
        self.assertEqual(self.bill.edit_reason, "Price correction")

        audit = BillEditAudit.objects.get()
        self.assertEqual(audit.bill_id, self.bill.pk)
        self.assertEqual(audit.edit_date, date(2026, 7, 17))
        self.assertEqual(audit.reason, "Price correction")

    def test_a_refused_edit_records_no_audit_row(self):
        """The audit is written in the same transaction as the rewrite, so a
        bill that didn't change must not carry a note saying it did."""
        response = self.post({
            "customer_id": self.nimal.pk,
            "lines": [{"product_id": self.pipe.pk, "qty": "2", "unit_price": "1000.00"}],
            "payment": {"type": "full_cash", "cash": "1.00", "account": ""},
        })
        self.assertEqual(response.status_code, 400)
        self.assertFalse(BillEditAudit.objects.exists())
        self.bill.refresh_from_db()
        self.assertIsNone(self.bill.edit_date)

    def test_each_edit_leaves_its_own_note(self):
        for reason in ("Wrong qty entered", "Price correction"):
            self.pass_gate(reason=reason)
            self.post({
                "customer_id": self.nimal.pk,
                "lines": [{"product_id": self.pipe.pk, "qty": "2", "unit_price": "1000.00"}],
                "payment": {"type": "full_cash", "cash": "7000.00", "account": ""},
            })

        self.assertEqual(
            list(BillEditAudit.objects.order_by("id").values_list("reason", flat=True)),
            ["Wrong qty entered", "Price correction"],
        )

    def test_the_detail_page_shows_the_last_edit(self):
        self.pass_gate(reason="Wrong qty entered")
        self.post({
            "customer_id": self.nimal.pk,
            "lines": [{"product_id": self.pipe.pk, "qty": "2", "unit_price": "1000.00"}],
            "payment": {"type": "full_cash", "cash": "7000.00", "account": ""},
        })
        response = self.client.get(reverse("core:bill_detail", args=[self.bill.pk]))
        self.assertContains(response, "Last edited:")
        self.assertContains(response, "Wrong qty entered")


class BillEditReasonGateTests(BillMutationMixin, TestCase):
    """Step 1 of an edit: when, and why, before the form is on screen."""

    def setUp(self):
        self.build()

    def url(self):
        return reverse("core:bill_edit", args=[self.bill.pk])

    def test_the_form_is_gated_until_a_reason_is_given(self):
        response = self.client.get(self.url())
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Why is this bill being edited?")
        self.assertContains(response, "Confirm and Continue")
        self.assertNotContains(response, 'id="bill-initial"')

    def test_the_gate_defaults_to_today(self):
        form = self.client.get(self.url()).context["form"]
        self.assertEqual(form.initial["edit_date"], timezone.localdate())

    def test_confirming_opens_the_edit_form(self):
        response = self.client.post(
            self.url(), {"edit_date": "2026-07-17", "reason": "Wrong qty entered"}
        )
        self.assertRedirects(response, self.url())
        self.assertContains(self.client.get(self.url()), 'id="bill-initial"')

    def test_a_blank_reason_is_refused(self):
        response = self.client.post(
            self.url(), {"edit_date": "2026-07-17", "reason": "   "}
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Give a reason for this edit.")
        self.assertNotIn(f"bill_edit_gate:{self.bill.pk}", self.client.session)

    def test_a_missing_date_is_refused(self):
        response = self.client.post(self.url(), {"edit_date": "", "reason": "Typo"})
        self.assertContains(response, "Enter the date of this edit.")
        self.assertNotIn(f"bill_edit_gate:{self.bill.pk}", self.client.session)

    def test_a_save_that_skipped_the_gate_is_refused(self):
        """The page posts JSON to the same URL, so the gate has to hold on the
        save and not only on the way in."""
        response = self.client.post(
            self.url(),
            json.dumps({
                "customer_id": self.nimal.pk,
                "lines": [{"product_id": self.pipe.pk, "qty": "2", "unit_price": "1000.00"}],
                "payment": {"type": "full_cash", "cash": "7000.00", "account": ""},
            }),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("needs a date and reason", response.json()["error"])
        self.assertFalse(BillEditAudit.objects.exists())
        self.bill.refresh_from_db()
        self.assertEqual(self.bill.total_amount, Decimal("1000.00"))

    def test_the_gate_is_spent_once_the_edit_saves(self):
        """The next edit is a new one and has to say why for itself."""
        self.client.post(self.url(), {"edit_date": "2026-07-17", "reason": "Typo"})
        self.client.post(
            self.url(),
            json.dumps({
                "customer_id": self.nimal.pk,
                "lines": [{"product_id": self.pipe.pk, "qty": "2", "unit_price": "1000.00"}],
                "payment": {"type": "full_cash", "cash": "7000.00", "account": ""},
            }),
            content_type="application/json",
        )
        self.assertNotIn(f"bill_edit_gate:{self.bill.pk}", self.client.session)
        self.assertContains(self.client.get(self.url()), "Why is this bill being edited?")

    def test_a_refused_save_keeps_the_gate(self):
        """The biller is going back to fix a figure, not to re-justify the
        same edit."""
        self.client.post(self.url(), {"edit_date": "2026-07-17", "reason": "Typo"})
        self.client.post(
            self.url(),
            json.dumps({
                "customer_id": self.nimal.pk,
                "lines": [{"product_id": self.pipe.pk, "qty": "2", "unit_price": "1000.00"}],
                "payment": {"type": "full_cash", "cash": "1.00", "account": ""},
            }),
            content_type="application/json",
        )
        self.assertIn(f"bill_edit_gate:{self.bill.pk}", self.client.session)

    def test_gates_are_kept_per_bill(self):
        """Two tabs on two bills must not wear each other's reason."""
        other = Bill.objects.create(
            customer=self.nimal,
            bill_date=date(2026, 7, 1),
            subtotal=Decimal("500.00"),
            total_amount=Decimal("500.00"),
            payment_type=Bill.PaymentType.PAY_LATER,
        )
        self.client.post(self.url(), {"edit_date": "2026-07-17", "reason": "Typo"})
        response = self.client.get(reverse("core:bill_edit", args=[other.pk]))
        self.assertContains(response, "Why is this bill being edited?")


class BillListTests(UserFactoryMixin, TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.nimal = Customer.objects.create(name="Nimal")
        cls.kamal = Customer.objects.create(name="Kamal Traders")

        def bill(customer, day, total, paid, payment_type, status):
            return Bill.objects.create(
                customer=customer,
                bill_date=date(2026, 6, day),
                subtotal=Decimal(total),
                total_amount=Decimal(total),
                paid_amount=Decimal(paid),
                balance_change=Decimal(paid) - Decimal(total),
                payment_type=payment_type,
                status=status,
            )

        cls.paid = bill(cls.nimal, 1, "1000.00", "1000.00",
                        Bill.PaymentType.FULL_CASH, Bill.Status.PAID)
        cls.unpaid = bill(cls.kamal, 5, "2000.00", "0.00",
                          Bill.PaymentType.PAY_LATER, Bill.Status.UNPAID)
        cls.partial = bill(cls.nimal, 9, "3000.00", "1200.00",
                           Bill.PaymentType.PARTIAL, Bill.Status.PARTIAL)
        # Paid past the bill: the extra cleared old debt.
        cls.overpaid = bill(cls.kamal, 12, "500.00", "4000.00",
                            Bill.PaymentType.FULL_CASH, Bill.Status.PAID)

    def setUp(self):
        self.client.force_login(self.make_manager())

    def rows(self, **params):
        response = self.client.get(reverse("core:bill_list"), params)
        self.response = response
        return {bill.pk: bill for bill in response.context["bills"]}

    def test_list_renders_every_bill(self):
        self.assertEqual(len(self.rows()), 4)
        self.assertEqual(self.response.status_code, 200)

    def test_outstanding_is_what_the_bill_still_owes(self):
        rows = self.rows()
        self.assertEqual(rows[self.unpaid.pk].outstanding, Decimal("2000.00"))
        self.assertEqual(rows[self.partial.pk].outstanding, Decimal("1800.00"))
        self.assertEqual(rows[self.paid.pk].outstanding, Decimal("0.00"))

    def test_outstanding_floors_at_zero_when_a_payment_cleared_old_debt(self):
        """4,000 against a 500 bill doesn't make the bill owe -3,500."""
        self.assertEqual(self.rows()[self.overpaid.pk].outstanding, Decimal("0.00"))

    def test_filter_by_date_range(self):
        rows = self.rows(from_date="2026-06-05", to_date="2026-06-09")
        self.assertEqual(set(rows), {self.unpaid.pk, self.partial.pk})

    def test_filter_by_customer(self):
        rows = self.rows(customer=self.nimal.pk)
        self.assertEqual(set(rows), {self.paid.pk, self.partial.pk})

    def test_filter_by_payment_type(self):
        self.assertEqual(set(self.rows(payment_type="pay_later")), {self.unpaid.pk})

    def test_filter_by_status(self):
        self.assertEqual(set(self.rows(status="partial")), {self.partial.pk})

    def test_filters_combine(self):
        rows = self.rows(customer=self.kamal.pk, status="unpaid")
        self.assertEqual(set(rows), {self.unpaid.pk})

    def test_unknown_filter_values_are_ignored_not_500s(self):
        rows = self.rows(payment_type="zzz", status="zzz", customer="zzz",
                         from_date="nonsense")
        self.assertEqual(len(rows), 4)
        self.assertFalse(self.response.context["is_filtered"])

    def test_empty_filter_result_renders_an_empty_state(self):
        response = self.client.get(reverse("core:bill_list"), {"status": "cancelled"})
        self.assertEqual(list(response.context["bills"]), [])
        self.assertContains(response, "No bills match your filters")

    def test_row_actions_are_offered(self):
        self.client.force_login(self.make_admin())
        response = self.client.get(reverse("core:bill_list"))
        self.assertContains(response, reverse("core:bill_detail", args=[self.paid.pk]))
        self.assertContains(response, reverse("core:bill_edit", args=[self.paid.pk]))
        self.assertContains(response, reverse("core:bill_delete", args=[self.paid.pk]))

    def test_list_requires_login(self):
        self.client.logout()
        response = self.client.get(reverse("core:bill_list"))
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("login"), response["Location"])


class ChequeModuleTests(UserFactoryMixin, TestCase):
    """Nimal owes 5,000. A 1,000 bill paid by a 6,000 cheque squares him: 1,000
    for the goods, 5,000 clearing the debt. If that cheque never becomes money,
    all 6,000 has to come back."""

    def setUp(self):
        self.client.force_login(self.make_manager())

        cat = Category.objects.create(name="Pipes")
        self.pipe = Product.objects.create(
            name="Pipe", category=cat,
            default_price=Decimal("1000.00"), qty=Decimal("10.000"),
        )
        self.nimal = Customer.objects.create(
            name="Nimal", balance=Decimal("-5000.00"), credit_limit=Decimal("50000.00")
        )
        self.client.post(
            reverse("core:bill_save"),
            json.dumps({
                "customer_id": self.nimal.pk,
                "lines": [{"product_id": self.pipe.pk, "qty": "1", "unit_price": "1000.00"}],
                "payment": {"type": "full_cheque", "cheque": {
                    "cheque_no": "C-1001", "bank_name": "BOC", "branch": "Galle",
                    "acc_no": "77", "amount": "6000.00",
                    "received_date": "2026-07-16", "maturity_date": "2026-08-16",
                }},
            }),
            content_type="application/json",
        )
        self.cheque = Cheque.objects.get()

    def balance(self):
        self.nimal.refresh_from_db()
        return self.nimal.balance

    # ---- the starting point ----
    def test_taking_the_cheque_squared_the_account(self):
        """Guards the fixture: the reversal tests mean nothing if this drifts."""
        self.assertEqual(self.balance(), Decimal("0.00"))
        self.assertEqual(self.cheque.status, Cheque.Status.PENDING)

    # ---- deposit ----
    def test_deposit_marks_it_without_touching_the_balance(self):
        response = self.client.post(
            reverse("core:cheque_deposit", args=[self.cheque.pk]), follow=True
        )
        self.assertRedirects(response, reverse("core:cheque_list"))
        self.cheque.refresh_from_db()
        self.assertEqual(self.cheque.status, Cheque.Status.DEPOSITED)
        # The credit went on when the cheque was taken; clearing only confirms it.
        self.assertEqual(self.balance(), Decimal("0.00"))

    # ---- hold ----
    def test_hold_gives_the_debt_back(self):
        self.client.post(reverse("core:cheque_hold", args=[self.cheque.pk]))
        self.cheque.refresh_from_db()
        self.assertEqual(self.cheque.status, Cheque.Status.HELD)
        # Not +6000: holding means we don't have the money, so he owes again.
        self.assertEqual(self.balance(), Decimal("-6000.00"))

    def test_hold_message_names_the_move(self):
        response = self.client.post(
            reverse("core:cheque_hold", args=[self.cheque.pk]), follow=True
        )
        msgs = [str(m) for m in response.context["messages"]]
        self.assertIn(
            "Cheque C-1001 marked held. Nimal owes 6000.00 again — "
            "balance is now -6000.00.",
            msgs,
        )

    # ---- bounce ----
    def test_bounce_records_the_new_date_and_gives_the_debt_back(self):
        self.client.post(
            reverse("core:cheque_bounce", args=[self.cheque.pk]),
            {"bounce_new_date": "2026-09-01"},
        )
        self.cheque.refresh_from_db()
        self.assertEqual(self.cheque.status, Cheque.Status.BOUNCED)
        self.assertEqual(self.cheque.bounce_new_date, date(2026, 9, 1))
        self.assertEqual(self.balance(), Decimal("-6000.00"))

    def test_bounce_without_a_date_changes_nothing(self):
        response = self.client.post(
            reverse("core:cheque_bounce", args=[self.cheque.pk]), {}, follow=True
        )
        msgs = [str(m) for m in response.context["messages"]]
        self.assertIn(
            "Enter the date the cheque is expected to be re-presented.", msgs
        )
        self.cheque.refresh_from_db()
        self.assertEqual(self.cheque.status, Cheque.Status.PENDING)
        self.assertEqual(self.balance(), Decimal("0.00"))

    def test_a_deposited_cheque_can_still_bounce(self):
        """It cleared, then came back. The credit has to come off just the same."""
        self.client.post(reverse("core:cheque_deposit", args=[self.cheque.pk]))
        self.assertEqual(self.balance(), Decimal("0.00"))

        self.client.post(
            reverse("core:cheque_bounce", args=[self.cheque.pk]),
            {"bounce_new_date": "2026-09-01"},
        )
        self.assertEqual(self.balance(), Decimal("-6000.00"))

    def test_re_presenting_a_bounced_cheque_puts_the_credit_back(self):
        self.client.post(
            reverse("core:cheque_bounce", args=[self.cheque.pk]),
            {"bounce_new_date": "2026-09-01"},
        )
        self.assertEqual(self.balance(), Decimal("-6000.00"))

        self.client.post(reverse("core:cheque_deposit", args=[self.cheque.pk]))
        self.assertEqual(self.balance(), Decimal("0.00"))

    def test_marking_the_same_status_twice_does_not_move_the_balance_twice(self):
        for _ in range(2):
            self.client.post(reverse("core:cheque_hold", args=[self.cheque.pk]))
        self.assertEqual(self.balance(), Decimal("-6000.00"))

    def test_actions_reject_get(self):
        for name in ("cheque_deposit", "cheque_hold", "cheque_bounce"):
            with self.subTest(action=name):
                response = self.client.get(reverse(f"core:{name}", args=[self.cheque.pk]))
                self.assertEqual(response.status_code, 405)
        self.assertEqual(self.balance(), Decimal("0.00"))

    def test_actions_require_login(self):
        self.client.logout()
        response = self.client.post(reverse("core:cheque_hold", args=[self.cheque.pk]))
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("login"), response["Location"])
        self.assertEqual(self.balance(), Decimal("0.00"))

    def test_actions_on_a_missing_cheque_404(self):
        self.assertEqual(
            self.client.post(reverse("core:cheque_hold", args=[9999])).status_code, 404
        )

    # ---- edit ----
    def test_edit_page_renders(self):
        response = self.client.get(reverse("core:cheque_edit", args=[self.cheque.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "C-1001")
        self.assertTrue(response.context["credited"])

    def payload(self, **overrides):
        data = {
            "cheque_no": "C-1001",
            "bank_name": "BOC",
            "branch": "Galle",
            "acc_no": "77",
            "amount": "6000.00",
            "received_date": "2026-07-16",
            "maturity_date": "2026-08-16",
            "status": "pending",
            "bounce_new_date": "",
        }
        data.update(overrides)
        return data

    def edit(self, **overrides):
        return self.client.post(
            reverse("core:cheque_edit", args=[self.cheque.pk]), self.payload(**overrides)
        )

    def test_editing_details_alone_leaves_the_balance_alone(self):
        self.edit(bank_name="HNB", branch="Colombo")
        self.cheque.refresh_from_db()
        self.assertEqual(self.cheque.bank_name, "HNB")
        self.assertEqual(self.balance(), Decimal("0.00"))

    def test_raising_the_amount_credits_the_difference(self):
        self.edit(amount="6500.00")
        # 500 more was received than we thought, so he is 500 in credit.
        self.assertEqual(self.balance(), Decimal("500.00"))

    def test_lowering_the_amount_takes_the_difference_back(self):
        self.edit(amount="5500.00")
        self.assertEqual(self.balance(), Decimal("-500.00"))

    def test_changing_the_status_through_the_form_moves_the_balance_too(self):
        self.edit(status="held")
        self.assertEqual(self.balance(), Decimal("-6000.00"))

    def test_amount_and_status_changing_together(self):
        """Held means none of it counts, whatever the amount is corrected to."""
        self.edit(amount="6500.00", status="held")
        self.assertEqual(self.balance(), Decimal("-6000.00"))

    def test_editing_the_amount_of_an_uncredited_cheque_leaves_the_balance(self):
        self.client.post(reverse("core:cheque_hold", args=[self.cheque.pk]))
        self.assertEqual(self.balance(), Decimal("-6000.00"))

        self.edit(amount="6500.00", status="held")
        # It isn't counted either way, so correcting it moves nothing.
        self.assertEqual(self.balance(), Decimal("-6000.00"))

    def test_bounced_without_a_new_date_is_rejected(self):
        response = self.edit(status="bounced", bounce_new_date="")
        self.assertEqual(response.status_code, 200)
        self.assertFormError(
            response.context["form"], "bounce_new_date",
            "A bounced cheque needs a new expected date.",
        )
        self.assertEqual(self.balance(), Decimal("0.00"))

    def test_backdated_maturity_is_rejected(self):
        response = self.edit(received_date="2026-07-16", maturity_date="2026-07-01")
        self.assertFormError(
            response.context["form"], "maturity_date",
            "Maturity date cannot be before the received date.",
        )
        self.assertEqual(self.balance(), Decimal("0.00"))

    def test_a_zero_amount_is_rejected(self):
        response = self.edit(amount="0")
        self.assertFormError(
            response.context["form"], "amount", "Cheque amount must be above 0."
        )
        self.assertEqual(self.balance(), Decimal("0.00"))

    def test_the_form_cannot_move_a_cheque_to_another_customer(self):
        other = Customer.objects.create(name="Kamal")
        self.client.post(
            reverse("core:cheque_edit", args=[self.cheque.pk]),
            self.payload(customer=other.pk),
        )
        self.cheque.refresh_from_db()
        self.assertEqual(self.cheque.customer, self.nimal)

    # ---- the ledger agrees with the account ----
    def test_a_bounced_cheque_drops_out_of_the_ledger(self):
        """The ledger's running total has to land where the account is, or the
        two tell the customer different stories."""
        response = self.client.get(reverse("core:customer_ledger", args=[self.nimal.pk]))
        self.assertEqual(response.context["closing_balance"], Decimal("-5000.00"))

        self.client.post(
            reverse("core:cheque_bounce", args=[self.cheque.pk]),
            {"bounce_new_date": "2026-09-01"},
        )
        response = self.client.get(reverse("core:customer_ledger", args=[self.nimal.pk]))
        rows = response.context["rows"]
        self.assertNotIn("Cheque received", [r["description"] for r in rows])
        # Ledger runs positive where the account runs negative.
        self.assertEqual(response.context["closing_balance"], Decimal("1000.00"))
        self.assertEqual(self.balance(), Decimal("-6000.00"))

    def test_a_held_cheque_drops_out_of_the_ledger_too(self):
        self.client.post(reverse("core:cheque_hold", args=[self.cheque.pk]))
        response = self.client.get(reverse("core:customer_ledger", args=[self.nimal.pk]))
        self.assertNotIn(
            "Cheque received", [r["description"] for r in response.context["rows"]]
        )

    def test_a_deposited_cheque_stays_in_the_ledger(self):
        self.client.post(reverse("core:cheque_deposit", args=[self.cheque.pk]))
        response = self.client.get(reverse("core:customer_ledger", args=[self.nimal.pk]))
        self.assertIn(
            "Cheque received", [r["description"] for r in response.context["rows"]]
        )


class ChequeListTests(UserFactoryMixin, TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.nimal = Customer.objects.create(name="Nimal")
        cls.kamal = Customer.objects.create(name="Kamal Traders")

        cat = Category.objects.create(name="Pipes")
        product = Product.objects.create(
            name="Pipe", category=cat, default_price=Decimal("100.00"), qty=Decimal("50.000")
        )
        bill = Bill.objects.create(
            customer=cls.nimal, bill_date=timezone.localdate(),
            payment_type=Bill.PaymentType.FULL_CHEQUE,
        )
        cls.payment = Payment.objects.create(
            bill=bill, method=Payment.Method.CHEQUE,
            amount=Decimal("100.00"), paid_at=timezone.now(),
        )
        cls.today = timezone.localdate()

        def cheque(no, customer, days, status, bank="BOC"):
            return Cheque.objects.create(
                payment=cls.payment, customer=customer, cheque_no=no,
                bank_name=bank, branch="Galle", amount=Decimal("100.00"),
                received_date=cls.today, maturity_date=cls.today + timedelta(days=days),
                status=status,
            )

        # Inside the 3-day window and still pending: the rows to act on.
        cls.due_today = cheque("DUE-TODAY", cls.nimal, 0, Cheque.Status.PENDING)
        cls.due_3 = cheque("DUE-3", cls.kamal, 3, Cheque.Status.PENDING)
        cls.overdue = cheque("OVERDUE", cls.nimal, -2, Cheque.Status.PENDING)
        # Outside it, one way or another.
        cls.day_4 = cheque("DAY-4", cls.nimal, 4, Cheque.Status.PENDING)
        cls.deposited = cheque("DEPOSITED", cls.kamal, 1, Cheque.Status.DEPOSITED)
        cls.bounced = cheque("BOUNCED", cls.nimal, 1, Cheque.Status.BOUNCED)
        cls.held = cheque("HELD", cls.kamal, 1, Cheque.Status.HELD)

    def setUp(self):
        self.client.force_login(self.make_manager())

    def rows(self, **params):
        response = self.client.get(reverse("core:cheque_list"), params)
        self.response = response
        return {c.cheque_no: c for c in response.context["cheques"]}

    def test_list_renders_every_cheque(self):
        self.assertEqual(len(self.rows()), 7)

    def test_only_pending_cheques_inside_the_window_are_flagged(self):
        rows = self.rows()
        flagged = {no for no, c in rows.items() if c.is_due_soon}
        self.assertEqual(flagged, {"DUE-TODAY", "DUE-3", "OVERDUE"})
        self.assertEqual(self.response.context["due_count"], 3)

    def test_a_deposited_cheque_maturing_today_is_not_flagged(self):
        """Already banked, so there's nothing to chase."""
        self.assertFalse(self.rows()["DEPOSITED"].is_due_soon)

    def test_flagged_rows_get_the_amber_background(self):
        response = self.client.get(reverse("core:cheque_list"))
        self.assertContains(response, "bg-amber-50 hover:bg-amber-100")

    def test_filter_by_status(self):
        self.assertEqual(set(self.rows(status="bounced")), {"BOUNCED"})

    def test_filter_by_customer(self):
        self.assertEqual(
            set(self.rows(customer=self.kamal.pk)), {"DUE-3", "DEPOSITED", "HELD"}
        )

    def test_filter_by_maturity_range(self):
        rows = self.rows(
            from_date=self.today.isoformat(),
            to_date=(self.today + timedelta(days=1)).isoformat(),
        )
        self.assertEqual(set(rows), {"DUE-TODAY", "DEPOSITED", "BOUNCED", "HELD"})

    def test_filters_combine(self):
        rows = self.rows(customer=self.nimal.pk, status="pending")
        self.assertEqual(set(rows), {"DUE-TODAY", "OVERDUE", "DAY-4"})

    def test_unknown_filter_values_are_ignored_not_500s(self):
        rows = self.rows(status="zzz", customer="zzz", from_date="nonsense")
        self.assertEqual(len(rows), 7)
        self.assertFalse(self.response.context["is_filtered"])

    def test_the_action_a_cheque_is_already_in_is_not_offered(self):
        html = self.client.get(reverse("core:cheque_list")).content.decode()
        self.assertNotIn(reverse("core:cheque_deposit", args=[self.deposited.pk]), html)
        self.assertNotIn(reverse("core:cheque_hold", args=[self.held.pk]), html)
        self.assertNotIn(reverse("core:cheque_bounce", args=[self.bounced.pk]), html)

    def test_every_other_action_is_offered(self):
        html = self.client.get(reverse("core:cheque_list")).content.decode()
        self.assertIn(reverse("core:cheque_deposit", args=[self.due_today.pk]), html)
        self.assertIn(reverse("core:cheque_hold", args=[self.due_today.pk]), html)
        self.assertIn(reverse("core:cheque_bounce", args=[self.due_today.pk]), html)
        self.assertIn(reverse("core:cheque_edit", args=[self.due_today.pk]), html)

    def test_actions_are_posts_carrying_csrf(self):
        html = self.client.get(reverse("core:cheque_list")).content.decode()
        self.assertIn('method="post"', html)
        self.assertIn("csrfmiddlewaretoken", html)

    def test_the_bounce_modal_says_what_the_balance_will_do(self):
        html = self.client.get(reverse("core:cheque_list")).content.decode()
        self.assertIn('id="bounce-modal"', html)
        self.assertIn('name="bounce_new_date"', html)
        # A credited cheque warns; one already held has nothing to reverse.
        self.assertIn('data-credited="yes"', html)
        self.assertIn('data-credited="no"', html)

    def test_customer_filter_lists_only_customers_with_cheques(self):
        Customer.objects.create(name="Nobody")
        names = [c.name for c in self.rows() and self.response.context["customers"]]
        self.assertNotIn("Nobody", names)

    def test_list_requires_login(self):
        self.client.logout()
        response = self.client.get(reverse("core:cheque_list"))
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("login"), response["Location"])


class ChequeDeleteTests(UserFactoryMixin, TestCase):
    """Deleting a cheque entered in error, as opposed to bouncing a real one."""

    def setUp(self):
        self.client.force_login(self.make_admin())

        cat = Category.objects.create(name="Pipes")
        self.pipe = Product.objects.create(
            name="Pipe", category=cat,
            default_price=Decimal("1000.00"), qty=Decimal("10.000"),
        )
        self.nimal = Customer.objects.create(
            name="Nimal", balance=Decimal("-5000.00"), credit_limit=Decimal("50000.00")
        )
        self.client.post(
            reverse("core:bill_save"),
            json.dumps({
                "customer_id": self.nimal.pk,
                "lines": [{"product_id": self.pipe.pk, "qty": "1", "unit_price": "1000.00"}],
                "payment": {"type": "full_cheque", "cheque": {
                    "cheque_no": "C-1001", "bank_name": "BOC", "branch": "", "acc_no": "",
                    "amount": "6000.00", "received_date": "2026-07-16",
                    "maturity_date": "2026-08-16",
                }},
            }),
            content_type="application/json",
        )
        self.cheque = Cheque.objects.get()

    def url(self, cheque=None):
        return reverse("core:cheque_delete", args=[(cheque or self.cheque).pk])

    def balance(self):
        self.nimal.refresh_from_db()
        return self.nimal.balance

    def test_deleting_a_pending_cheque_gives_the_debt_back(self):
        response = self.client.post(self.url(), follow=True)
        self.assertRedirects(response, reverse("core:cheque_list"))
        self.assertFalse(Cheque.objects.exists())
        # Not +6000: the cheque never became money, so he owes it again.
        self.assertEqual(self.balance(), Decimal("-6000.00"))

        msgs = [str(m) for m in response.context["messages"]]
        self.assertIn(
            "Cheque C-1001 was deleted. Nimal owes 6000.00 again — "
            "balance is now -6000.00.",
            msgs,
        )

    def test_deleting_takes_the_payment_with_it(self):
        self.assertTrue(Payment.objects.exists())
        self.client.post(self.url())
        self.assertFalse(Payment.objects.exists())
        self.assertFalse(Cheque.objects.exists())

    def test_a_deposited_cheque_cannot_be_deleted(self):
        self.client.post(reverse("core:cheque_deposit", args=[self.cheque.pk]))
        response = self.client.post(self.url(), follow=True)

        self.assertTrue(Cheque.objects.filter(pk=self.cheque.pk).exists())
        self.assertTrue(Payment.objects.exists())
        self.assertEqual(self.balance(), Decimal("0.00"))
        msgs = [str(m) for m in response.context["messages"]]
        self.assertIn(
            "Cheque C-1001 has been deposited, so it can't be deleted. "
            "The money is in the bank — mark it bounced if it came back.",
            msgs,
        )

    def test_deleting_a_bounced_cheque_does_not_reverse_twice(self):
        """Bouncing already took the credit off; deleting must not take it
        again."""
        self.client.post(
            reverse("core:cheque_bounce", args=[self.cheque.pk]),
            {"bounce_new_date": "2026-09-01"},
        )
        self.assertEqual(self.balance(), Decimal("-6000.00"))

        self.client.post(self.url())
        self.assertEqual(self.balance(), Decimal("-6000.00"))
        self.assertFalse(Cheque.objects.exists())

    def test_deleting_a_held_cheque_does_not_reverse_twice(self):
        self.client.post(reverse("core:cheque_hold", args=[self.cheque.pk]))
        self.client.post(self.url())
        self.assertEqual(self.balance(), Decimal("-6000.00"))

    def test_manager_cannot_delete_a_cheque(self):
        self.client.force_login(self.make_manager())
        response = self.client.post(self.url())
        self.assertRedirects(response, reverse("core:dashboard"))
        self.assertTrue(Cheque.objects.exists())
        self.assertEqual(self.balance(), Decimal("0.00"))

    def test_manager_gets_no_delete_control(self):
        self.client.force_login(self.make_manager())
        html = self.client.get(reverse("core:cheque_list")).content.decode()
        self.assertNotIn(self.url(), html)
        self.assertNotIn('id="delete-modal"', html)

    def test_a_deposited_cheque_is_offered_no_delete_button(self):
        self.client.post(reverse("core:cheque_deposit", args=[self.cheque.pk]))
        html = self.client.get(reverse("core:cheque_list")).content.decode()
        self.assertNotIn(f'data-delete-url="{self.url()}"', html)

    def test_delete_rejects_get(self):
        self.assertEqual(self.client.get(self.url()).status_code, 405)
        self.assertTrue(Cheque.objects.exists())

    def test_delete_missing_cheque_404s(self):
        self.assertEqual(
            self.client.post(reverse("core:cheque_delete", args=[9999])).status_code, 404
        )


class NotifyChequesCommandTests(TestCase):
    """The command is built for cron, so its exit code is the interface."""

    @classmethod
    def setUpTestData(cls):
        cls.today = timezone.localdate()
        cls.nimal = Customer.objects.create(name="Nimal Stores")

        cat = Category.objects.create(name="Pipes")
        Product.objects.create(
            name="Pipe", category=cat, default_price=Decimal("100.00"), qty=Decimal("50.000")
        )
        bill = Bill.objects.create(
            customer=cls.nimal, bill_date=cls.today,
            payment_type=Bill.PaymentType.FULL_CHEQUE,
        )
        cls.payment = Payment.objects.create(
            bill=bill, method=Payment.Method.CHEQUE,
            amount=Decimal("100.00"), paid_at=timezone.now(),
        )

    def cheque(self, no, days, status=Cheque.Status.PENDING, amount="1500.00"):
        return Cheque.objects.create(
            payment=self.payment, customer=self.nimal, cheque_no=no,
            bank_name="BOC", amount=Decimal(amount),
            received_date=self.today,
            maturity_date=self.today + timedelta(days=days),
            status=status,
        )

    def run_command(self, **options):
        out = StringIO()
        try:
            call_command("notify_cheques", stdout=out, **options)
        except SystemExit as exc:
            return out.getvalue(), exc.code
        return out.getvalue(), 0

    def test_exits_zero_and_says_so_when_there_is_nothing_to_chase(self):
        output, code = self.run_command()
        self.assertEqual(code, 0)
        self.assertIn("No pending cheques maturing", output)

    def test_exits_one_when_cheques_are_found(self):
        """Non-zero is the whole point: cron reads it."""
        self.cheque("C-1", 1)
        output, code = self.run_command()
        self.assertEqual(code, 1)

    def test_table_carries_every_column(self):
        self.cheque("C-1", 2, amount="1500.00")
        output, _ = self.run_command()
        for heading in ("Customer", "Cheque No", "Bank", "Amount", "Maturity Date", "Days Left"):
            with self.subTest(heading=heading):
                self.assertIn(heading, output)
        self.assertIn("Nimal Stores", output)
        self.assertIn("C-1", output)
        self.assertIn("BOC", output)
        self.assertIn("1,500.00", output)
        self.assertIn((self.today + timedelta(days=2)).strftime("%Y-%m-%d"), output)

    def test_days_left_reads_plainly(self):
        self.cheque("DUE-TODAY", 0)
        self.cheque("DUE-2", 2)
        self.cheque("LATE", -3)
        output, _ = self.run_command()
        self.assertIn("today", output)
        self.assertIn("3 overdue", output)

    def test_summary_totals_the_cheques(self):
        self.cheque("C-1", 1, amount="1500.00")
        self.cheque("C-2", 2, amount="2500.00")
        output, _ = self.run_command()
        self.assertIn("2 pending cheques maturing", output)
        self.assertIn("4,000.00", output)

    def test_only_pending_cheques_inside_the_window_are_reported(self):
        self.cheque("IN-WINDOW", 3)
        self.cheque("OVERDUE", -5)
        self.cheque("TOO-FAR", 4)
        self.cheque("DEPOSITED", 1, status=Cheque.Status.DEPOSITED)
        self.cheque("BOUNCED", 1, status=Cheque.Status.BOUNCED)
        self.cheque("HELD", 1, status=Cheque.Status.HELD)

        output, code = self.run_command()
        self.assertEqual(code, 1)
        self.assertIn("IN-WINDOW", output)
        self.assertIn("OVERDUE", output)
        self.assertNotIn("TOO-FAR", output)
        self.assertNotIn("DEPOSITED", output)
        self.assertNotIn("BOUNCED", output)
        self.assertNotIn("HELD", output)

    def test_days_option_widens_the_window(self):
        self.cheque("DAY-6", 6)
        _, code = self.run_command()
        self.assertEqual(code, 0)

        output, code = self.run_command(days=7)
        self.assertEqual(code, 1)
        self.assertIn("DAY-6", output)

    def test_the_command_matches_what_the_dashboard_shows(self):
        """Two places asking the same question must not disagree."""
        self.cheque("IN-WINDOW", 1)
        self.cheque("OVERDUE", -5)
        self.cheque("TOO-FAR", 9)
        self.cheque("HELD", 1, status=Cheque.Status.HELD)

        User = get_user_model()
        User.objects.create_user(username="dash", password="pw", role=User.Role.MANAGER)
        self.client.login(username="dash", password="pw")
        dashboard = self.client.get(reverse("core:dashboard"))
        on_dashboard = {c.cheque_no for c in dashboard.context["maturing_cheques"]}

        output, _ = self.run_command()
        in_command = {
            no for no in ("IN-WINDOW", "OVERDUE", "TOO-FAR", "HELD") if no in output
        }
        self.assertEqual(on_dashboard, in_command)
        self.assertEqual(in_command, {"IN-WINDOW", "OVERDUE"})

    # ---- email ----
    @override_settings(
        ADMINS=[("Owner", "owner@senovka.local")],
        EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
    )
    def test_emails_the_admins_when_configured(self):
        self.cheque("C-1", 1, amount="1500.00")
        output, code = self.run_command()

        self.assertEqual(code, 1)
        self.assertEqual(len(mail.outbox), 1)
        sent = mail.outbox[0]
        self.assertIn("1 pending cheque maturing", sent.subject)
        self.assertIn("C-1", sent.body)
        self.assertIn("1,500.00", sent.body)
        self.assertEqual(sent.to, ["owner@senovka.local"])
        self.assertIn("Emailed owner@senovka.local.", output)

    @override_settings(ADMINS=[])
    def test_says_plainly_when_no_email_could_be_sent(self):
        """Silence would leave the operator unsure whether mail went out."""
        self.cheque("C-1", 1)
        output, _ = self.run_command()
        self.assertEqual(len(mail.outbox), 0)
        self.assertIn("ADMINS is not configured, so no email was sent.", output)

    @override_settings(
        ADMINS=[("Owner", "owner@senovka.local")],
        EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
    )
    def test_no_email_option_prints_only(self):
        self.cheque("C-1", 1)
        output, code = self.run_command(no_email=True)
        self.assertEqual(code, 1)
        self.assertEqual(len(mail.outbox), 0)
        self.assertIn("C-1", output)

    @override_settings(ADMINS=[("Owner", "owner@senovka.local")])
    def test_no_email_is_sent_when_there_is_nothing_to_report(self):
        _, code = self.run_command()
        self.assertEqual(code, 0)
        self.assertEqual(len(mail.outbox), 0)


class CashDrawerPageTests(UserFactoryMixin, TestCase):
    """Every figure below is hand-computed.

    5,000 in, 1,200 out, 800 transferred => 3,000 in the drawer.
    """

    def setUp(self):
        self.client.force_login(self.make_manager())
        self.today = timezone.localdate()

        def entry(day, kind, amount, reason="", bill=None):
            return CashDrawer.objects.create(
                txn_date=date(2026, 6, day), txn_type=kind,
                amount=Decimal(amount), reason=reason, bill=bill,
            )

        self.nimal = Customer.objects.create(name="Nimal")
        self.bill = Bill.objects.create(
            customer=self.nimal, bill_date=date(2026, 6, 1),
            total_amount=Decimal("5000.00"),
            payment_type=Bill.PaymentType.FULL_CASH,
        )
        self.in_entry = entry(1, CashDrawer.TxnType.IN, "5000.00",
                              reason="Bill #%s cash" % self.bill.pk, bill=self.bill)
        self.out_entry = entry(5, CashDrawer.TxnType.OUT, "1200.00",
                               reason="Owner Withdrawal — school fees")
        self.transfer_entry = entry(9, CashDrawer.TxnType.TRANSFER, "800.00",
                                    reason="Bill cash to Senovka")

    def page(self, **params):
        response = self.client.get(reverse("core:cash_drawer"), params)
        self.response = response
        return response.context

    # ---- balance ----
    def test_balance_nets_in_against_out_and_transfer(self):
        self.assertEqual(self.page()["balance"], Decimal("3000.00"))

    def test_balance_ignores_the_date_filter(self):
        """The drawer holds what it holds, whatever range is on screen."""
        ctx = self.page(from_date="2026-06-09", to_date="2026-06-09")
        self.assertEqual(ctx["balance"], Decimal("3000.00"))
        self.assertEqual(len(ctx["rows"]), 1)

    def test_account_totals_come_from_banked_bill_payments(self):
        bill = Bill.objects.create(
            customer=self.nimal, bill_date=self.today,
            payment_type=Bill.PaymentType.FULL_CASH,
        )
        payment = Payment.objects.create(
            bill=bill, method=Payment.Method.CASH,
            amount=Decimal("800.00"), paid_at=timezone.now(),
        )
        CashTransfer.objects.create(
            payment=payment, to_account=CashTransfer.Account.SENOVKA,
            amount=Decimal("800.00"), transferred_at=timezone.now(),
        )
        CashTransfer.objects.create(
            payment=payment, to_account=CashTransfer.Account.DINUSHA,
            amount=Decimal("250.00"), transferred_at=timezone.now(),
        )
        ctx = self.page()
        self.assertEqual(ctx["senovka_banked"], Decimal("800.00"))
        self.assertEqual(ctx["dinusha_banked"], Decimal("250.00"))

    def test_a_manual_transfer_does_not_reach_the_account_totals(self):
        """The accepted trade-off of having no account column on CashDrawer.
        The page says so in words rather than let the figure look wrong."""
        self.client.post(reverse("core:cash_drawer"), {
            "txn_date": "2026-06-10", "kind": "senovka",
            "amount": "500.00", "reason": "banked at BOC",
        })
        ctx = self.page()
        self.assertEqual(ctx["senovka_banked"], Decimal("0.00"))
        self.assertEqual(ctx["balance"], Decimal("2500.00"))
        self.assertContains(self.response, "Banked from bill payments only.")

    # ---- the log ----
    def test_rows_run_oldest_first_with_a_cumulative_balance(self):
        rows = self.page()["rows"]
        self.assertEqual(
            [(r["entry"].txn_date, r["is_in"], r["running"]) for r in rows],
            [
                (date(2026, 6, 1), True, Decimal("5000.00")),
                (date(2026, 6, 5), False, Decimal("3800.00")),
                (date(2026, 6, 9), False, Decimal("3000.00")),
            ],
        )

    def test_the_last_running_balance_is_the_drawer_balance(self):
        ctx = self.page()
        self.assertEqual(ctx["closing"], ctx["balance"])

    def test_totals_split_in_from_out(self):
        ctx = self.page()
        self.assertEqual(ctx["total_in"], Decimal("5000.00"))
        # Withdrawal and transfer both leave the drawer.
        self.assertEqual(ctx["total_out"], Decimal("2000.00"))

    def test_a_filtered_range_carries_an_opening_balance(self):
        """Starting the column at zero would report a drawer that never was."""
        ctx = self.page(from_date="2026-06-05")
        self.assertEqual(ctx["opening"], Decimal("5000.00"))
        self.assertEqual([r["running"] for r in ctx["rows"]],
                         [Decimal("3800.00"), Decimal("3000.00")])
        self.assertEqual(ctx["closing"], ctx["balance"])
        self.assertContains(self.response, "Opening balance")

    def test_an_unfiltered_log_opens_at_zero(self):
        self.assertEqual(self.page()["opening"], Decimal("0.00"))

    def test_bill_linked_rows_point_at_the_bill(self):
        self.page()
        self.assertContains(
            self.response, reverse("core:bill_detail", args=[self.bill.pk])
        )
        self.assertContains(self.response, "Nimal")

    def test_manual_rows_show_their_reason(self):
        self.page()
        self.assertContains(self.response, "Owner Withdrawal — school fees")

    def test_unparsable_dates_are_ignored_not_500s(self):
        ctx = self.page(from_date="nonsense", to_date="2026-13-45")
        self.assertEqual(len(ctx["rows"]), 3)
        self.assertFalse(ctx["is_filtered"])

    # ---- recording money out ----
    def test_recording_a_withdrawal_takes_it_off_the_drawer(self):
        response = self.client.post(reverse("core:cash_drawer"), {
            "txn_date": "2026-06-10", "kind": "withdrawal",
            "amount": "500.00", "reason": "petrol",
        }, follow=True)
        self.assertRedirects(response, reverse("core:cash_drawer"))

        entry = CashDrawer.objects.latest("id")
        self.assertEqual(entry.txn_type, CashDrawer.TxnType.OUT)
        self.assertEqual(entry.amount, Decimal("500.00"))
        self.assertEqual(entry.txn_date, date(2026, 6, 10))
        self.assertEqual(entry.reason, "Owner Withdrawal — petrol")
        self.assertIsNone(entry.bill)
        self.assertEqual(views._cash_drawer_balance(), Decimal("2500.00"))

    def test_the_type_is_named_in_the_reason(self):
        """The only place a manual transfer's destination can be recorded."""
        for kind, expected in (
            ("senovka", "Transfer to Senovka Account — banked"),
            ("dinusha", "Transfer to Dinusha Account — banked"),
        ):
            with self.subTest(kind=kind):
                self.client.post(reverse("core:cash_drawer"), {
                    "txn_date": "2026-06-10", "kind": kind,
                    "amount": "10.00", "reason": "banked",
                })
                self.assertEqual(CashDrawer.objects.latest("id").reason, expected)

    def test_a_blank_reason_still_names_the_type(self):
        self.client.post(reverse("core:cash_drawer"), {
            "txn_date": "2026-06-10", "kind": "withdrawal",
            "amount": "10.00", "reason": "",
        })
        self.assertEqual(CashDrawer.objects.latest("id").reason, "Owner Withdrawal")

    def test_the_message_says_what_is_left(self):
        response = self.client.post(reverse("core:cash_drawer"), {
            "txn_date": "2026-06-10", "kind": "withdrawal",
            "amount": "500.00", "reason": "petrol",
        }, follow=True)
        msgs = [str(m) for m in response.context["messages"]]
        self.assertIn(
            "Owner Withdrawal — petrol — 500.00 out of the drawer. 2,500.00 left.",
            msgs,
        )

    def test_more_cash_than_the_drawer_holds_is_refused(self):
        """A drawer cannot hold minus two thousand rupees."""
        response = self.client.post(reverse("core:cash_drawer"), {
            "txn_date": "2026-06-10", "kind": "withdrawal",
            "amount": "5000.00", "reason": "too much",
        })
        self.assertEqual(response.status_code, 200)
        self.assertFormError(
            response.context["form"], "amount", "Only 3,000.00 is in the drawer."
        )
        self.assertEqual(CashDrawer.objects.count(), 3)
        self.assertEqual(views._cash_drawer_balance(), Decimal("3000.00"))

    def test_taking_the_whole_drawer_is_allowed(self):
        self.client.post(reverse("core:cash_drawer"), {
            "txn_date": "2026-06-10", "kind": "withdrawal",
            "amount": "3000.00", "reason": "all of it",
        })
        self.assertEqual(views._cash_drawer_balance(), Decimal("0.00"))

    def test_a_zero_amount_is_refused(self):
        response = self.client.post(reverse("core:cash_drawer"), {
            "txn_date": "2026-06-10", "kind": "withdrawal",
            "amount": "0", "reason": "nothing",
        })
        self.assertFormError(
            response.context["form"], "amount", "Amount must be above 0."
        )
        self.assertEqual(CashDrawer.objects.count(), 3)

    def test_the_form_cannot_put_money_in(self):
        """Cash arrives by saving a bill. Nothing here may type it in."""
        self.client.post(reverse("core:cash_drawer"), {
            "txn_date": "2026-06-10", "kind": "withdrawal",
            "amount": "100.00", "reason": "x", "txn_type": "in",
        })
        self.assertEqual(CashDrawer.objects.latest("id").txn_type, CashDrawer.TxnType.OUT)
        self.assertEqual(views._cash_drawer_balance(), Decimal("2900.00"))

    def test_the_form_cannot_attach_an_entry_to_a_bill(self):
        self.client.post(reverse("core:cash_drawer"), {
            "txn_date": "2026-06-10", "kind": "withdrawal",
            "amount": "100.00", "reason": "x", "bill": self.bill.pk,
        })
        self.assertIsNone(CashDrawer.objects.latest("id").bill)

    def test_the_date_defaults_to_today(self):
        ctx = self.page()
        self.assertEqual(ctx["form"].initial["txn_date"], self.today)

    def test_page_requires_login(self):
        self.client.logout()
        response = self.client.get(reverse("core:cash_drawer"))
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("login"), response["Location"])

    def test_recording_requires_login(self):
        self.client.logout()
        self.client.post(reverse("core:cash_drawer"), {
            "txn_date": "2026-06-10", "kind": "withdrawal", "amount": "10.00",
        })
        self.assertEqual(CashDrawer.objects.count(), 3)

    # ---- editing ----
    def edit(self, entry, **overrides):
        data = {
            "txn_date": entry.txn_date.isoformat(),
            "txn_type": entry.txn_type,
            "amount": str(entry.amount),
            "reason": entry.reason,
            "edit_reason": "Wrong amount keyed",
            **overrides,
        }
        return self.client.post(
            reverse("core:cash_drawer_edit", args=[entry.pk]), data
        )

    def test_editing_a_manual_entry_updates_it(self):
        response = self.edit(self.out_entry, amount="900.00", txn_date="2026-06-06")
        self.assertRedirects(response, reverse("core:cash_drawer"))

        self.out_entry.refresh_from_db()
        self.assertEqual(self.out_entry.amount, Decimal("900.00"))
        self.assertEqual(self.out_entry.txn_date, date(2026, 6, 6))

    def test_editing_stamps_who_changed_it_and_why(self):
        self.edit(self.out_entry, amount="900.00", edit_reason="Recount")
        self.out_entry.refresh_from_db()
        self.assertEqual(self.out_entry.edit_reason, "Recount")
        self.assertEqual(self.out_entry.edited_by.username, "t_admin")
        self.assertIsNotNone(self.out_entry.edited_at)

    def test_an_edit_re_reckons_the_running_balance(self):
        """Nothing is reversed on save: the column is summed from the rows on
        every render, so the corrected figure simply counts differently."""
        self.edit(self.out_entry, amount="200.00")
        ctx = self.page()
        # 5000 in, 200 out, 800 transferred.
        self.assertEqual(ctx["balance"], Decimal("4000.00"))
        self.assertEqual(ctx["closing"], Decimal("4000.00"))

    def test_the_type_can_be_corrected(self):
        self.edit(self.out_entry, txn_type=CashDrawer.TxnType.TRANSFER)
        self.out_entry.refresh_from_db()
        self.assertEqual(self.out_entry.txn_type, CashDrawer.TxnType.TRANSFER)

    def test_an_edit_needs_a_reason(self):
        response = self.edit(self.out_entry, amount="900.00", edit_reason="   ")
        self.assertEqual(response.status_code, 200)
        self.assertFormError(
            response.context["edit_form"], "edit_reason",
            "Give a reason for this edit.",
        )
        self.out_entry.refresh_from_db()
        self.assertEqual(self.out_entry.amount, Decimal("1200.00"))

    def test_an_edit_cannot_take_out_more_than_the_drawer_would_hold(self):
        """Judged against the drawer without this entry — putting the original
        1200 back leaves 4200 available."""
        response = self.edit(self.out_entry, amount="4300.00")
        self.assertEqual(response.status_code, 200)
        self.assertFormError(
            response.context["edit_form"], "amount",
            "Only 4,200.00 would be in the drawer without this entry.",
        )
        self.out_entry.refresh_from_db()
        self.assertEqual(self.out_entry.amount, Decimal("1200.00"))

    def test_raising_a_withdrawal_within_the_drawer_is_allowed(self):
        self.edit(self.out_entry, amount="4200.00")
        self.out_entry.refresh_from_db()
        self.assertEqual(self.out_entry.amount, Decimal("4200.00"))
        self.assertEqual(self.page()["balance"], Decimal("0.00"))

    def test_a_failed_edit_comes_back_with_the_page_intact(self):
        """It re-renders the whole log, so the running balance and the filters
        have to come back with it rather than 500."""
        response = self.edit(self.out_entry, edit_reason="")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["edit_entry"], self.out_entry)
        self.assertEqual(len(response.context["rows"]), 3)
        self.assertEqual(response.context["balance"], Decimal("3000.00"))

    def test_a_bill_linked_entry_cannot_be_edited(self):
        response = self.edit(self.in_entry, amount="1.00")
        self.assertRedirects(response, reverse("core:cash_drawer"))
        self.in_entry.refresh_from_db()
        self.assertEqual(self.in_entry.amount, Decimal("5000.00"))

    def test_edit_refuses_a_get(self):
        response = self.client.get(
            reverse("core:cash_drawer_edit", args=[self.out_entry.pk])
        )
        self.assertRedirects(response, reverse("core:cash_drawer"))

    def test_editing_requires_login(self):
        self.client.logout()
        self.edit(self.out_entry, amount="1.00")
        self.out_entry.refresh_from_db()
        self.assertEqual(self.out_entry.amount, Decimal("1200.00"))

    # ---- deleting ----
    def test_deleting_a_manual_entry_removes_it(self):
        response = self.client.post(
            reverse("core:cash_drawer_delete", args=[self.out_entry.pk])
        )
        self.assertRedirects(response, reverse("core:cash_drawer"))
        self.assertFalse(CashDrawer.objects.filter(pk=self.out_entry.pk).exists())

    def test_deleting_re_reckons_the_balance_with_no_reversal(self):
        self.client.post(
            reverse("core:cash_drawer_delete", args=[self.out_entry.pk])
        )
        # 5000 in, 800 transferred — the 1200 withdrawal simply stops counting.
        self.assertEqual(self.page()["balance"], Decimal("4200.00"))

    def test_a_bill_linked_entry_cannot_be_deleted(self):
        response = self.client.post(
            reverse("core:cash_drawer_delete", args=[self.in_entry.pk])
        )
        self.assertRedirects(response, reverse("core:cash_drawer"))
        self.assertTrue(CashDrawer.objects.filter(pk=self.in_entry.pk).exists())

    def test_deleting_a_drawer_entry_leaves_its_bill_alone(self):
        self.client.post(
            reverse("core:cash_drawer_delete", args=[self.transfer_entry.pk])
        )
        self.assertTrue(Bill.objects.filter(pk=self.bill.pk).exists())

    def test_delete_refuses_a_get(self):
        response = self.client.get(
            reverse("core:cash_drawer_delete", args=[self.out_entry.pk])
        )
        self.assertEqual(response.status_code, 405)

    def test_deleting_requires_login(self):
        self.client.logout()
        self.client.post(
            reverse("core:cash_drawer_delete", args=[self.out_entry.pk])
        )
        self.assertTrue(CashDrawer.objects.filter(pk=self.out_entry.pk).exists())

    # ---- the actions column ----
    def test_only_manual_rows_are_marked_editable(self):
        rows = {r["entry"].pk: r["is_manual"] for r in self.page()["rows"]}
        self.assertFalse(rows[self.in_entry.pk])
        self.assertTrue(rows[self.out_entry.pk])
        self.assertTrue(rows[self.transfer_entry.pk])

    def test_a_bill_linked_row_offers_no_buttons(self):
        self.page()
        html = self.response.content.decode()
        self.assertNotIn(
            reverse("core:cash_drawer_edit", args=[self.in_entry.pk]), html
        )
        self.assertNotIn(
            reverse("core:cash_drawer_delete", args=[self.in_entry.pk]), html
        )
        self.assertIn(f"From Bill #{self.bill.pk}", html)

    def test_a_manual_row_offers_both_buttons(self):
        self.page()
        html = self.response.content.decode()
        self.assertIn(reverse("core:cash_drawer_edit", args=[self.out_entry.pk]), html)
        self.assertIn(reverse("core:cash_drawer_delete", args=[self.out_entry.pk]), html)

    def test_an_edited_row_shows_the_badge_and_its_reason(self):
        self.edit(self.out_entry, amount="900.00", edit_reason="Recount")
        self.page()
        html = self.response.content.decode()
        self.assertIn("Edited", html)
        self.assertIn("Recount", html)

    def test_the_log_takes_fifty_to_a_page(self):
        self.assertEqual(
            self.page()["page_obj"].paginator.per_page, settings.PAGINATE_BY_REPORTS
        )


class CashDrawerBillIntegrationTests(UserFactoryMixin, TestCase):
    """The page has to agree with what 5D writes when a bill is saved."""

    def setUp(self):
        self.client.force_login(self.make_manager())
        cat = Category.objects.create(name="Pipes")
        self.pipe = Product.objects.create(
            name="Pipe", category=cat,
            default_price=Decimal("1000.00"), qty=Decimal("10.000"),
        )
        self.nimal = Customer.objects.create(
            name="Nimal", balance=Decimal("0.00"), credit_limit=Decimal("50000.00")
        )

    def save_bill(self, account=""):
        return self.client.post(
            reverse("core:bill_save"),
            json.dumps({
                "customer_id": self.nimal.pk,
                "lines": [{"product_id": self.pipe.pk, "qty": "2", "unit_price": "1000.00"}],
                "payment": {"type": "full_cash", "cash": "2000.00", "account": account},
            }),
            content_type="application/json",
        )

    def test_a_cash_bill_shows_up_as_money_in(self):
        self.save_bill()
        ctx = self.client.get(reverse("core:cash_drawer")).context
        self.assertEqual(ctx["balance"], Decimal("2000.00"))
        self.assertEqual(len(ctx["rows"]), 1)
        self.assertTrue(ctx["rows"][0]["is_in"])

    def test_a_banked_cash_bill_nets_to_nothing_and_shows_both_legs(self):
        """In then straight out: the drawer is level and the log says why."""
        self.save_bill(account="senovka")
        ctx = self.client.get(reverse("core:cash_drawer")).context

        self.assertEqual(ctx["balance"], Decimal("0.00"))
        self.assertEqual([r["is_in"] for r in ctx["rows"]], [True, False])
        self.assertEqual([r["running"] for r in ctx["rows"]],
                         [Decimal("2000.00"), Decimal("0.00")])
        # This one does reach the account total: a bill payment wrote a
        # CashTransfer alongside it.
        self.assertEqual(ctx["senovka_banked"], Decimal("2000.00"))

    def test_deleting_the_bill_empties_the_drawer_again(self):
        self.save_bill()
        self.client.force_login(self.make_admin())
        self.client.post(reverse("core:bill_delete", args=[Bill.objects.get().pk]))

        ctx = self.client.get(reverse("core:cash_drawer")).context
        self.assertEqual(ctx["balance"], Decimal("0.00"))
        self.assertEqual(ctx["rows"], [])


class SupplierBillTests(UserFactoryMixin, TestCase):
    """A supplier bill is the mirror of a sales bill: stock comes in, and the
    balance moves positive because we now owe them."""

    def setUp(self):
        self.client.force_login(self.make_admin())
        cat = Category.objects.create(name="Pipes")
        self.category = cat
        self.pipe = Product.objects.create(
            name="Pipe", size="50mm", category=cat,
            default_price=Decimal("1000.00"), qty=Decimal("10.000"),
        )
        self.tank = Product.objects.create(
            name="Tank", category=cat,
            default_price=Decimal("500.00"), qty=Decimal("4.000"),
        )
        self.supplier = Customer.objects.create(
            name="Lanka Polymers", is_supplier=True, balance=Decimal("0.00")
        )
        self.customer = Customer.objects.create(name="Nimal", is_supplier=False)

    def payload(self, lines=None, supplier=None):
        return {
            "supplier_id": (supplier or self.supplier).pk,
            "lines": lines if lines is not None else [
                {"product_id": self.pipe.pk, "qty": "5", "unit_price": "600.00"}
            ],
        }

    def save(self, payload=None):
        return self.client.post(
            reverse("core:supplier_bill_create"),
            json.dumps(payload if payload is not None else self.payload()),
            content_type="application/json",
        )

    def balance(self):
        self.supplier.refresh_from_db()
        return self.supplier.balance

    # ---- saving ----
    def test_saving_writes_the_bill_and_its_lines(self):
        response = self.save()
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["success"])

        bill = SupplierBill.objects.get()
        self.assertEqual(bill.supplier, self.supplier)
        self.assertEqual(bill.total_amount, Decimal("3000.00"))
        self.assertEqual(bill.bill_date, timezone.localdate())
        self.assertEqual(bill.status, SupplierBill.Status.UNPAID)

        item = SupplierBillItem.objects.get()
        self.assertEqual(item.qty, Decimal("5.000"))
        self.assertEqual(item.unit_price, Decimal("600.00"))
        self.assertEqual(item.line_total, Decimal("3000.00"))

    def test_receiving_adds_to_stock(self):
        self.save()
        self.pipe.refresh_from_db()
        self.assertEqual(self.pipe.qty, Decimal("15.000"))

    def test_the_supplier_balance_moves_positive(self):
        """Positive is credit in their favour: we owe them for the delivery."""
        self.save()
        self.assertEqual(self.balance(), Decimal("3000.00"))

    def test_a_second_bill_stacks_on_the_first(self):
        self.save()
        self.save()
        self.assertEqual(self.balance(), Decimal("6000.00"))
        self.pipe.refresh_from_db()
        self.assertEqual(self.pipe.qty, Decimal("20.000"))

    def test_line_totals_are_recomputed_not_taken_from_the_browser(self):
        self.save(self.payload(lines=[
            {"product_id": self.pipe.pk, "qty": "5", "unit_price": "600.00",
             "line_total": "1.00"}
        ]))
        self.assertEqual(SupplierBillItem.objects.get().line_total, Decimal("3000.00"))
        self.assertEqual(SupplierBill.objects.get().total_amount, Decimal("3000.00"))

    def test_a_bill_can_carry_several_lines(self):
        self.save(self.payload(lines=[
            {"product_id": self.pipe.pk, "qty": "5", "unit_price": "600.00"},
            {"product_id": self.tank.pk, "qty": "2", "unit_price": "300.00"},
        ]))
        self.assertEqual(SupplierBill.objects.get().total_amount, Decimal("3600.00"))
        self.assertEqual(self.balance(), Decimal("3600.00"))
        self.tank.refresh_from_db()
        self.assertEqual(self.tank.qty, Decimal("6.000"))

    # ---- validation ----
    def test_a_non_supplier_cannot_be_billed_from(self):
        response = self.save(self.payload(supplier=self.customer))
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"], "Choose a supplier.")
        self.assertFalse(SupplierBill.objects.exists())

    def test_an_empty_bill_is_refused(self):
        response = self.save(self.payload(lines=[]))
        self.assertEqual(response.status_code, 400)
        self.assertIn("at least one product line", response.json()["error"])

    def test_the_same_product_twice_is_refused(self):
        response = self.save(self.payload(lines=[
            {"product_id": self.pipe.pk, "qty": "1", "unit_price": "600.00"},
            {"product_id": self.pipe.pk, "qty": "2", "unit_price": "600.00"},
        ]))
        self.assertEqual(response.status_code, 400)
        self.assertIn("on the bill twice", response.json()["error"])

    def test_a_zero_quantity_is_refused(self):
        response = self.save(self.payload(lines=[
            {"product_id": self.pipe.pk, "qty": "0", "unit_price": "600.00"}
        ]))
        self.assertEqual(response.status_code, 400)
        self.assertIn("must be above 0", response.json()["error"])

    def test_a_refused_bill_writes_nothing(self):
        self.save(self.payload(lines=[
            {"product_id": self.pipe.pk, "qty": "5", "unit_price": "600.00"},
            {"product_id": self.tank.pk, "qty": "-1", "unit_price": "300.00"},
        ]))
        self.assertFalse(SupplierBill.objects.exists())
        self.assertFalse(SupplierBillItem.objects.exists())
        self.pipe.refresh_from_db()
        self.assertEqual(self.pipe.qty, Decimal("10.000"))
        self.assertEqual(self.balance(), Decimal("0.00"))

    def test_junk_payloads_are_refused_not_500s(self):
        for body in ["not json", json.dumps([]), ""]:
            with self.subTest(body=body[:10]):
                response = self.client.post(
                    reverse("core:supplier_bill_create"), body,
                    content_type="application/json",
                )
                self.assertEqual(response.status_code, 400)
        self.assertFalse(SupplierBill.objects.exists())

    # ---- inline creation ----
    def test_a_supplier_can_be_created_inline(self):
        response = self.client.post(reverse("core:supplier_quick_create"), {
            "name": "  New Supplies  ", "phone": " 077 ", "address": "Galle",
        })
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertTrue(body["success"])

        supplier = Customer.objects.get(name="New Supplies")
        self.assertTrue(supplier.is_supplier)
        self.assertTrue(supplier.is_active)
        self.assertEqual(supplier.balance, Decimal("0.00"))
        self.assertEqual(supplier.phone, "077")
        self.assertEqual(body["supplier"], {"id": supplier.pk, "name": "New Supplies"})

    def test_a_new_supplier_starts_at_zero_then_takes_the_bill(self):
        response = self.client.post(reverse("core:supplier_quick_create"), {
            "name": "New Supplies", "phone": "", "address": "",
        })
        new_id = response.json()["supplier"]["id"]
        self.save({
            "supplier_id": new_id,
            "lines": [{"product_id": self.pipe.pk, "qty": "5", "unit_price": "600.00"}],
        })
        self.assertEqual(
            Customer.objects.get(pk=new_id).balance, Decimal("3000.00")
        )

    def test_a_nameless_supplier_is_refused(self):
        response = self.client.post(reverse("core:supplier_quick_create"), {"name": ""})
        self.assertEqual(response.status_code, 400)
        self.assertIn("name", response.json()["errors"])

    def test_a_product_can_be_created_inline(self):
        response = self.client.post(reverse("core:product_quick_create"), {
            "name": "Elbow", "size": "50mm",
            "category": self.category.pk, "default_price": "45",
        })
        self.assertEqual(response.status_code, 200)
        product = Product.objects.get(name="Elbow")
        self.assertTrue(product.is_active)
        self.assertEqual(product.qty, Decimal("0.000"))
        self.assertEqual(
            response.json()["product"],
            {
                "id": product.pk, "name": "Elbow", "size": "50mm",
                "label": "Elbow 50mm", "default_price": "45.00",
            },
        )

    def test_the_inline_product_form_still_rejects_duplicates(self):
        """The shortcut must not be a way around the name+size rule."""
        response = self.client.post(reverse("core:product_quick_create"), {
            "name": "Pipe", "size": "50mm",
            "category": self.category.pk, "default_price": "45",
        })
        self.assertEqual(response.status_code, 400)
        self.assertIn("__all__", response.json()["errors"])
        self.assertEqual(Product.objects.filter(name="Pipe").count(), 1)

    def test_inline_creation_rejects_get(self):
        for name in ("supplier_quick_create", "product_quick_create"):
            with self.subTest(endpoint=name):
                self.assertEqual(self.client.get(reverse(f"core:{name}")).status_code, 405)

    def test_inline_creation_requires_login(self):
        self.client.logout()
        self.client.post(reverse("core:supplier_quick_create"), {"name": "Sneaky"})
        self.assertFalse(Customer.objects.filter(name="Sneaky").exists())

    # ---- editing ----
    def test_resaving_an_unchanged_bill_changes_nothing(self):
        """The reversal and re-apply must cancel exactly, or every open and
        save quietly doubles the delivery."""
        self.save()
        bill = SupplierBill.objects.get()

        response = self.client.post(
            reverse("core:supplier_bill_edit", args=[bill.pk]),
            json.dumps(self.payload()), content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)

        self.pipe.refresh_from_db()
        self.assertEqual(self.pipe.qty, Decimal("15.000"))
        self.assertEqual(self.balance(), Decimal("3000.00"))
        self.assertEqual(SupplierBill.objects.count(), 1)
        self.assertEqual(SupplierBillItem.objects.count(), 1)

    def test_editing_the_quantity_moves_stock_by_the_difference(self):
        self.save()
        bill = SupplierBill.objects.get()
        self.client.post(
            reverse("core:supplier_bill_edit", args=[bill.pk]),
            json.dumps(self.payload(lines=[
                {"product_id": self.pipe.pk, "qty": "8", "unit_price": "600.00"}
            ])),
            content_type="application/json",
        )
        self.pipe.refresh_from_db()
        self.assertEqual(self.pipe.qty, Decimal("18.000"))
        self.assertEqual(self.balance(), Decimal("4800.00"))

    def test_editing_keeps_the_original_bill_date(self):
        self.save()
        bill = SupplierBill.objects.get()
        bill.bill_date = date(2026, 1, 5)
        bill.save(update_fields=["bill_date"])

        self.client.post(
            reverse("core:supplier_bill_edit", args=[bill.pk]),
            json.dumps(self.payload()), content_type="application/json",
        )
        self.assertEqual(SupplierBill.objects.get().bill_date, date(2026, 1, 5))

    def test_the_edit_page_carries_the_bill_back_into_the_form(self):
        self.save()
        bill = SupplierBill.objects.get()
        response = self.client.get(reverse("core:supplier_bill_edit", args=[bill.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["initial"]["supplier_id"], self.supplier.pk)
        self.assertEqual(response.context["initial"]["lines"], [
            {"product_id": self.pipe.pk, "qty": "5", "unit_price": "600.00"}
        ])

    def test_a_refused_edit_leaves_the_original_intact(self):
        self.save()
        bill = SupplierBill.objects.get()
        response = self.client.post(
            reverse("core:supplier_bill_edit", args=[bill.pk]),
            json.dumps(self.payload(lines=[])), content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)

        self.pipe.refresh_from_db()
        self.assertEqual(self.pipe.qty, Decimal("15.000"))
        self.assertEqual(self.balance(), Decimal("3000.00"))
        self.assertEqual(SupplierBillItem.objects.count(), 1)

    # ---- deleting ----
    def test_delete_reverses_stock_and_balance(self):
        self.save()
        bill = SupplierBill.objects.get()
        response = self.client.post(
            reverse("core:supplier_bill_delete", args=[bill.pk]), follow=True
        )
        self.assertRedirects(response, reverse("core:supplier_bill_list"))

        self.pipe.refresh_from_db()
        self.assertEqual(self.pipe.qty, Decimal("10.000"))
        self.assertEqual(self.balance(), Decimal("0.00"))
        self.assertFalse(SupplierBill.objects.exists())
        self.assertFalse(SupplierBillItem.objects.exists())

    def test_stock_already_sold_on_blocks_the_reversal(self):
        """Taking it back regardless would leave the product holding a negative
        quantity, and it would vanish from every sales screen."""
        self.save()
        bill = SupplierBill.objects.get()

        # 15 received; sell 12, leaving 3 of the 5 this bill brought in.
        self.pipe.qty = Decimal("3.000")
        self.pipe.save(update_fields=["qty"])

        response = self.client.post(
            reverse("core:supplier_bill_delete", args=[bill.pk]), follow=True
        )
        self.assertRedirects(
            response, reverse("core:supplier_bill_detail", args=[bill.pk])
        )
        self.assertTrue(SupplierBill.objects.filter(pk=bill.pk).exists())

        msgs = [str(m) for m in response.context["messages"]]
        self.assertTrue(any("has been sold on" in m for m in msgs), msgs)

        # Nothing moved.
        self.pipe.refresh_from_db()
        self.assertEqual(self.pipe.qty, Decimal("3.000"))
        self.assertEqual(self.balance(), Decimal("3000.00"))

    def test_a_blocked_reversal_rolls_back_earlier_lines(self):
        """The first line's stock goes back before the second one fails."""
        self.save(self.payload(lines=[
            {"product_id": self.pipe.pk, "qty": "5", "unit_price": "600.00"},
            {"product_id": self.tank.pk, "qty": "2", "unit_price": "300.00"},
        ]))
        bill = SupplierBill.objects.get()

        self.tank.qty = Decimal("0.000")  # both received tanks sold on
        self.tank.save(update_fields=["qty"])

        self.client.post(reverse("core:supplier_bill_delete", args=[bill.pk]))

        self.pipe.refresh_from_db()
        self.assertEqual(self.pipe.qty, Decimal("15.000"))  # not taken back
        self.assertTrue(SupplierBill.objects.filter(pk=bill.pk).exists())
        self.assertEqual(self.balance(), Decimal("3600.00"))

    def test_manager_cannot_delete_a_supplier_bill(self):
        self.save()
        bill = SupplierBill.objects.get()
        self.client.force_login(self.make_manager())
        response = self.client.post(reverse("core:supplier_bill_delete", args=[bill.pk]))
        self.assertRedirects(response, reverse("core:dashboard"))
        self.assertTrue(SupplierBill.objects.exists())

    def test_delete_rejects_get(self):
        self.save()
        bill = SupplierBill.objects.get()
        self.assertEqual(
            self.client.get(reverse("core:supplier_bill_delete", args=[bill.pk])).status_code,
            405,
        )

    # ---- pages ----
    def test_the_detail_page_shows_the_bill_and_links_to_the_ledger(self):
        self.save()
        bill = SupplierBill.objects.get()
        response = self.client.get(reverse("core:supplier_bill_detail", args=[bill.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Lanka Polymers")
        self.assertContains(response, "Pipe")
        self.assertContains(response, "3,000.00")
        self.assertContains(
            response, reverse("core:customer_ledger", args=[self.supplier.pk])
        )

    def test_the_create_page_offers_only_suppliers(self):
        response = self.client.get(reverse("core:supplier_bill_create"))
        names = [s.name for s in response.context["suppliers"]]
        self.assertEqual(names, ["Lanka Polymers"])
        self.assertNotIn("Nimal", names)

    def test_the_create_page_carries_the_product_catalogue(self):
        response = self.client.get(reverse("core:supplier_bill_create"))
        catalogue = {p["label"]: p for p in response.context["products_json"]}
        self.assertEqual(set(catalogue), {"Pipe 50mm", "Tank"})
        self.assertEqual(catalogue["Pipe 50mm"]["default_price"], "1000.00")

    def test_an_inactive_product_is_not_offered(self):
        self.pipe.is_active = False
        self.pipe.save(update_fields=["is_active"])
        response = self.client.get(reverse("core:supplier_bill_create"))
        labels = [p["label"] for p in response.context["products_json"]]
        self.assertNotIn("Pipe 50mm", labels)

    def test_pages_require_login(self):
        self.client.logout()
        for url in (
            reverse("core:supplier_bill_list"),
            reverse("core:supplier_bill_create"),
        ):
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertEqual(response.status_code, 302)
                self.assertIn(reverse("login"), response["Location"])


class SupplierBillListTests(UserFactoryMixin, TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.lanka = Customer.objects.create(name="Lanka Polymers", is_supplier=True)
        cls.ceylon = Customer.objects.create(name="Ceylon Resins", is_supplier=True)

        cat = Category.objects.create(name="Pipes")
        cls.pipe = Product.objects.create(
            name="Pipe", category=cat, default_price=Decimal("100.00"), qty=Decimal("0.000")
        )

        def bill(supplier, day, total, status):
            made = SupplierBill.objects.create(
                supplier=supplier, bill_date=date(2026, 6, day),
                total_amount=Decimal(total), status=status,
            )
            SupplierBillItem.objects.create(
                supplier_bill=made, product=cls.pipe,
                qty=Decimal("1.000"), unit_price=Decimal(total),
                line_total=Decimal(total),
            )
            return made

        cls.early = bill(cls.lanka, 1, "1000.00", SupplierBill.Status.UNPAID)
        cls.mid = bill(cls.ceylon, 5, "2000.00", SupplierBill.Status.PAID)
        cls.late = bill(cls.lanka, 9, "3000.00", SupplierBill.Status.UNPAID)

    def setUp(self):
        self.client.force_login(self.make_manager())

    def rows(self, **params):
        response = self.client.get(reverse("core:supplier_bill_list"), params)
        self.response = response
        return {b.pk: b for b in response.context["bills"]}

    def test_list_renders_every_bill(self):
        self.assertEqual(len(self.rows()), 3)

    def test_items_are_counted(self):
        self.assertEqual(self.rows()[self.early.pk].item_count, 1)

    def test_filter_by_date_range(self):
        rows = self.rows(from_date="2026-06-05", to_date="2026-06-09")
        self.assertEqual(set(rows), {self.mid.pk, self.late.pk})

    def test_filter_by_supplier(self):
        rows = self.rows(supplier=self.lanka.pk)
        self.assertEqual(set(rows), {self.early.pk, self.late.pk})

    def test_filter_by_status(self):
        self.assertEqual(set(self.rows(status="paid")), {self.mid.pk})

    def test_unknown_filter_values_are_ignored_not_500s(self):
        rows = self.rows(status="zzz", supplier="zzz", from_date="nonsense")
        self.assertEqual(len(rows), 3)
        self.assertFalse(self.response.context["is_filtered"])

    def test_row_actions_are_offered(self):
        self.client.force_login(self.make_admin())
        response = self.client.get(reverse("core:supplier_bill_list"))
        self.assertContains(response, reverse("core:supplier_bill_detail", args=[self.early.pk]))
        self.assertContains(response, reverse("core:supplier_bill_edit", args=[self.early.pk]))
        self.assertContains(response, reverse("core:supplier_bill_delete", args=[self.early.pk]))

    def test_manager_gets_no_delete_control(self):
        html = self.client.get(reverse("core:supplier_bill_list")).content.decode()
        self.assertNotIn(reverse("core:supplier_bill_delete", args=[self.early.pk]), html)
        self.assertNotIn('id="delete-modal"', html)


class ProductionTests(UserFactoryMixin, TestCase):
    """Production is the other way stock reaches the shelf. Pipe starts at 10,
    Tank at 4."""

    def setUp(self):
        self.client.force_login(self.make_admin())
        cat = Category.objects.create(name="Pipes")
        self.pipe = Product.objects.create(
            name="Pipe", size="50mm", category=cat,
            default_price=Decimal("1000.00"), qty=Decimal("10.000"),
        )
        self.tank = Product.objects.create(
            name="Tank", category=cat,
            default_price=Decimal("500.00"), qty=Decimal("4.000"),
        )
        self.today = timezone.localdate()

    def payload(self, lines=None, production_date=None):
        if lines is None:
            lines = [{"product_id": self.pipe.pk, "qty_produced": "5"}]

        # A reason is required on any row that produced something, so give every
        # line one unless the test is saying something about the reason itself.
        lines = [
            {"reason": "Morning production run", **line}
            if line.get("qty_produced") not in (None, "0")
            else line
            for line in lines
        ]

        return {
            "production_date": (production_date or self.today).isoformat()
            if not isinstance(production_date, str)
            else production_date,
            "lines": lines,
        }

    def save(self, payload=None):
        return self.client.post(
            reverse("core:production_create"),
            json.dumps(payload if payload is not None else self.payload()),
            content_type="application/json",
        )

    def edit(self, entry, **overrides):
        """POST the edit form. Every field is required, so the unchanged ones
        have to be posted back alongside whatever the test is changing."""
        data = {
            "production_date": entry.production_date.isoformat(),
            "qty_produced": str(entry.qty_produced),
            "reason": entry.reason,
            **overrides,
        }
        return self.client.post(
            reverse("core:production_edit", args=[entry.pk]), data
        )

    def stock(self, product):
        product.refresh_from_db()
        return product.qty

    # ---- saving ----
    def test_saving_writes_the_entry_and_adds_the_stock(self):
        response = self.save()
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["success"])

        entry = ProductionEntry.objects.get()
        self.assertEqual(entry.product, self.pipe)
        self.assertEqual(entry.production_date, self.today)
        self.assertEqual(entry.qty_produced, Decimal("5.000"))
        self.assertEqual(self.stock(self.pipe), Decimal("15.000"))

    def test_the_snapshot_records_the_shelf_either_side(self):
        self.save()
        entry = ProductionEntry.objects.get()
        self.assertEqual(entry.stock_before, Decimal("10.000"))
        self.assertEqual(entry.stock_after, Decimal("15.000"))

    def test_the_snapshot_holds_still_when_stock_moves_on(self):
        """That is the whole point of storing it: Product.qty is one running
        number, so a later sale would otherwise erase what production found."""
        self.save()
        self.pipe.qty = Decimal("2.000")  # sold since
        self.pipe.save(update_fields=["qty"])

        entry = ProductionEntry.objects.get()
        self.assertEqual(entry.stock_before, Decimal("10.000"))
        self.assertEqual(entry.stock_after, Decimal("15.000"))

    def test_only_rows_with_a_quantity_are_saved(self):
        self.save(self.payload(lines=[
            {"product_id": self.pipe.pk, "qty_produced": "5"},
            {"product_id": self.tank.pk, "qty_produced": "0"},
        ]))
        self.assertEqual(ProductionEntry.objects.count(), 1)
        self.assertEqual(ProductionEntry.objects.get().product, self.pipe)
        self.assertEqual(self.stock(self.tank), Decimal("4.000"))

    def test_a_whole_sheet_of_zeroes_is_refused(self):
        response = self.save(self.payload(lines=[
            {"product_id": self.pipe.pk, "qty_produced": "0"},
            {"product_id": self.tank.pk, "qty_produced": "0"},
        ]))
        self.assertEqual(response.status_code, 400)
        self.assertIn("at least one product", response.json()["error"])
        self.assertFalse(ProductionEntry.objects.exists())

    def test_several_products_in_one_day(self):
        self.save(self.payload(lines=[
            {"product_id": self.pipe.pk, "qty_produced": "5"},
            {"product_id": self.tank.pk, "qty_produced": "2.5"},
        ]))
        self.assertEqual(ProductionEntry.objects.count(), 2)
        self.assertEqual(self.stock(self.pipe), Decimal("15.000"))
        self.assertEqual(self.stock(self.tank), Decimal("6.500"))

    def test_production_can_be_backdated(self):
        past = self.today - timedelta(days=5)
        self.save(self.payload(production_date=past))
        self.assertEqual(ProductionEntry.objects.get().production_date, past)
        # The date labels the entry; the stock still lands on today's shelf.
        self.assertEqual(self.stock(self.pipe), Decimal("15.000"))

    def test_production_cannot_be_dated_in_the_future(self):
        response = self.save(
            self.payload(production_date=self.today + timedelta(days=1))
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("future", response.json()["error"])
        self.assertFalse(ProductionEntry.objects.exists())

    def test_a_missing_date_is_refused(self):
        response = self.save({"production_date": "", "lines": [
            {"product_id": self.pipe.pk, "qty_produced": "5"}
        ]})
        self.assertEqual(response.status_code, 400)
        self.assertIn("production date", response.json()["error"])

    def test_the_reason_is_stored_against_the_entry(self):
        self.save(self.payload(lines=[
            {"product_id": self.pipe.pk, "qty_produced": "5",
             "reason": "Evening batch"},
        ]))
        self.assertEqual(ProductionEntry.objects.get().reason, "Evening batch")

    def test_a_row_with_a_quantity_and_no_reason_is_refused(self):
        response = self.save(self.payload(lines=[
            {"product_id": self.pipe.pk, "qty_produced": "5", "reason": ""},
        ]))
        self.assertEqual(response.status_code, 400)
        self.assertIn("reason", response.json()["error"])
        self.assertFalse(ProductionEntry.objects.exists())
        self.assertEqual(self.stock(self.pipe), Decimal("10.000"))

    def test_a_reason_of_only_spaces_is_refused(self):
        response = self.save(self.payload(lines=[
            {"product_id": self.pipe.pk, "qty_produced": "5", "reason": "   "},
        ]))
        self.assertEqual(response.status_code, 400)
        self.assertIn("reason", response.json()["error"])
        self.assertFalse(ProductionEntry.objects.exists())

    def test_a_row_at_zero_needs_no_reason(self):
        """Rows left at zero are just the shelf sitting there; they are never
        saved, so there is nothing to explain."""
        response = self.save(self.payload(lines=[
            {"product_id": self.pipe.pk, "qty_produced": "5"},
            {"product_id": self.tank.pk, "qty_produced": "0", "reason": ""},
        ]))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(ProductionEntry.objects.count(), 1)

    def test_one_row_missing_a_reason_writes_none_of_the_sheet(self):
        response = self.save(self.payload(lines=[
            {"product_id": self.pipe.pk, "qty_produced": "5"},
            {"product_id": self.tank.pk, "qty_produced": "2", "reason": ""},
        ]))
        self.assertEqual(response.status_code, 400)
        self.assertFalse(ProductionEntry.objects.exists())
        self.assertEqual(self.stock(self.pipe), Decimal("10.000"))
        self.assertEqual(self.stock(self.tank), Decimal("4.000"))

    def test_an_over_long_reason_is_refused_not_a_500(self):
        """The column holds 500. Without this the save would raise on the way
        into the database instead of coming back as a message."""
        response = self.save(self.payload(lines=[
            {"product_id": self.pipe.pk, "qty_produced": "5", "reason": "x" * 501},
        ]))
        self.assertEqual(response.status_code, 400)
        self.assertFalse(ProductionEntry.objects.exists())

    def test_the_same_product_twice_is_refused(self):
        response = self.save(self.payload(lines=[
            {"product_id": self.pipe.pk, "qty_produced": "5"},
            {"product_id": self.pipe.pk, "qty_produced": "2"},
        ]))
        self.assertEqual(response.status_code, 400)
        self.assertIn("on the sheet twice", response.json()["error"])

    def test_a_negative_quantity_is_refused_not_a_500(self):
        response = self.save(self.payload(lines=[
            {"product_id": self.pipe.pk, "qty_produced": "-5"}
        ]))
        self.assertEqual(response.status_code, 400)
        self.assertIn("cannot be negative", response.json()["error"])

    def test_a_refused_sheet_writes_nothing(self):
        self.save(self.payload(lines=[
            {"product_id": self.pipe.pk, "qty_produced": "5"},
            {"product_id": self.tank.pk, "qty_produced": "nonsense"},
        ]))
        self.assertFalse(ProductionEntry.objects.exists())
        self.assertEqual(self.stock(self.pipe), Decimal("10.000"))

    def test_junk_payloads_are_refused_not_500s(self):
        for body in ["not json", json.dumps([]), ""]:
            with self.subTest(body=body[:10]):
                response = self.client.post(
                    reverse("core:production_create"), body,
                    content_type="application/json",
                )
                self.assertEqual(response.status_code, 400)

    # ---- editing ----
    def test_raising_the_quantity_adds_the_difference(self):
        self.save()
        entry = ProductionEntry.objects.get()
        self.edit(entry, qty_produced="8")
        entry.refresh_from_db()
        self.assertEqual(entry.qty_produced, Decimal("8.000"))
        self.assertEqual(self.stock(self.pipe), Decimal("18.000"))

    def test_lowering_the_quantity_takes_the_difference_back(self):
        self.save()
        entry = ProductionEntry.objects.get()
        self.edit(entry, qty_produced="2")
        self.assertEqual(self.stock(self.pipe), Decimal("12.000"))

    def test_editing_keeps_stock_before_and_moves_stock_after(self):
        """stock_before is what the entry found on the day, and correcting the
        quantity now cannot change that. What it left behind does change."""
        self.save()
        entry = ProductionEntry.objects.get()
        self.edit(entry, qty_produced="8")
        entry.refresh_from_db()
        self.assertEqual(entry.stock_before, Decimal("10.000"))
        self.assertEqual(entry.stock_after, Decimal("18.000"))

    def test_lowering_below_what_is_left_on_the_shelf_is_refused(self):
        self.save()
        entry = ProductionEntry.objects.get()

        self.pipe.qty = Decimal("1.000")  # sold since
        self.pipe.save(update_fields=["qty"])

        response = self.edit(entry, qty_produced="1")
        self.assertEqual(response.status_code, 200)
        self.assertFormError(
            response.context["form"], "qty_produced",
            "Can't take 4.000 back off Pipe 50mm — only 1.000 is left, so some "
            "of it has been sold.",
        )
        entry.refresh_from_db()
        self.assertEqual(entry.qty_produced, Decimal("5.000"))
        self.assertEqual(self.stock(self.pipe), Decimal("1.000"))

    def test_a_zero_quantity_is_refused_on_edit(self):
        self.save()
        entry = ProductionEntry.objects.get()
        response = self.edit(entry, qty_produced="0")
        self.assertFormError(
            response.context["form"], "qty_produced",
            "Quantity must be above 0. Delete the entry instead.",
        )
        self.assertEqual(self.stock(self.pipe), Decimal("15.000"))

    def test_the_reason_is_editable(self):
        self.save()
        entry = ProductionEntry.objects.get()
        self.edit(entry, reason="Recount correction")
        entry.refresh_from_db()
        self.assertEqual(entry.reason, "Recount correction")

    def test_a_blank_reason_is_refused_on_edit(self):
        self.save()
        entry = ProductionEntry.objects.get()
        response = self.edit(entry, reason="   ")
        self.assertFormError(
            response.context["form"], "reason",
            "Give a reason for this production.",
        )
        entry.refresh_from_db()
        self.assertEqual(entry.reason, "Morning production run")

    def test_the_date_is_editable_and_moves_no_stock(self):
        """The shelf is one running figure, not a per-day one, so moving an
        entry to another day cannot move any of it."""
        self.save()
        entry = ProductionEntry.objects.get()
        past = self.today - timedelta(days=3)

        self.edit(entry, production_date=past.isoformat())
        entry.refresh_from_db()
        self.assertEqual(entry.production_date, past)
        self.assertEqual(self.stock(self.pipe), Decimal("15.000"))

    def test_a_future_date_is_refused_on_edit(self):
        self.save()
        entry = ProductionEntry.objects.get()
        response = self.edit(
            entry, production_date=(self.today + timedelta(days=1)).isoformat()
        )
        self.assertFormError(
            response.context["form"], "production_date",
            "Production can't be dated in the future.",
        )
        entry.refresh_from_db()
        self.assertEqual(entry.production_date, self.today)

    def test_the_edit_page_renders(self):
        self.save()
        entry = ProductionEntry.objects.get()
        response = self.client.get(reverse("core:production_edit", args=[entry.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Pipe 50mm")
        self.assertEqual(response.context["form"].initial["qty_produced"], Decimal("5.000"))

    def test_a_manager_may_edit(self):
        self.save()
        entry = ProductionEntry.objects.get()
        self.client.force_login(self.make_manager())
        response = self.client.get(reverse("core:production_edit", args=[entry.pk]))
        self.assertEqual(response.status_code, 200)

    # ---- deleting ----
    def test_delete_takes_the_stock_back_off(self):
        self.save()
        entry = ProductionEntry.objects.get()
        response = self.client.post(
            reverse("core:production_delete", args=[entry.pk]), follow=True
        )
        self.assertRedirects(response, reverse("core:production_list"))
        self.assertFalse(ProductionEntry.objects.exists())
        self.assertEqual(self.stock(self.pipe), Decimal("10.000"))

    def test_delete_is_refused_when_the_stock_has_been_sold(self):
        self.save()
        entry = ProductionEntry.objects.get()

        self.pipe.qty = Decimal("3.000")  # only 3 of the 15 left
        self.pipe.save(update_fields=["qty"])

        response = self.client.post(
            reverse("core:production_delete", args=[entry.pk]), follow=True
        )
        self.assertTrue(ProductionEntry.objects.filter(pk=entry.pk).exists())
        self.assertEqual(self.stock(self.pipe), Decimal("3.000"))
        msgs = [str(m) for m in response.context["messages"]]
        self.assertTrue(any("has been sold" in m for m in msgs), msgs)

    def test_manager_cannot_delete(self):
        self.save()
        entry = ProductionEntry.objects.get()
        self.client.force_login(self.make_manager())
        response = self.client.post(reverse("core:production_delete", args=[entry.pk]))
        self.assertRedirects(response, reverse("core:dashboard"))
        self.assertTrue(ProductionEntry.objects.exists())

    def test_delete_rejects_get(self):
        self.save()
        entry = ProductionEntry.objects.get()
        self.assertEqual(
            self.client.get(reverse("core:production_delete", args=[entry.pk])).status_code,
            405,
        )

    # ---- the create page ----
    def test_the_create_page_lists_active_products_with_their_stock(self):
        response = self.client.get(reverse("core:production_create"))
        self.assertEqual(response.status_code, 200)
        names = [p.name for p in response.context["products"]]
        self.assertEqual(sorted(names), ["Pipe", "Tank"])
        self.assertContains(response, 'data-stock="10.000"')

    def test_an_inactive_product_is_not_on_the_sheet(self):
        self.pipe.is_active = False
        self.pipe.save(update_fields=["is_active"])
        response = self.client.get(reverse("core:production_create"))
        self.assertNotIn("Pipe", [p.name for p in response.context["products"]])

    def test_the_date_defaults_to_today_and_cannot_be_future(self):
        response = self.client.get(reverse("core:production_create"))
        stamp = self.today.isoformat()
        self.assertContains(response, f'value="{stamp}" max="{stamp}"')

    def test_pages_require_login(self):
        self.client.logout()
        for url in (
            reverse("core:production_list"),
            reverse("core:production_create"),
        ):
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertEqual(response.status_code, 302)
                self.assertIn(reverse("login"), response["Location"])


class ProductionListTests(UserFactoryMixin, TestCase):
    @classmethod
    def setUpTestData(cls):
        cat = Category.objects.create(name="Pipes")
        cls.pipe = Product.objects.create(
            name="Pipe", category=cat, default_price=Decimal("100.00"), qty=Decimal("0.000")
        )
        cls.tank = Product.objects.create(
            name="Tank", category=cat, default_price=Decimal("500.00"), qty=Decimal("0.000")
        )

        def entry(product, day, qty, before="0"):
            return ProductionEntry.objects.create(
                product=product, production_date=date(2026, 6, day),
                qty_produced=Decimal(qty),
                stock_before=Decimal(before),
                stock_after=Decimal(before) + Decimal(qty),
            )

        # 1 Jun: two products. 5 Jun: one. 9 Jun: one.
        cls.jun1_pipe = entry(cls.pipe, 1, "10")
        cls.jun1_tank = entry(cls.tank, 1, "5")
        cls.jun5_pipe = entry(cls.pipe, 5, "3", before="10")
        cls.jun9_tank = entry(cls.tank, 9, "7", before="5")

    def setUp(self):
        self.client.force_login(self.make_manager())

    def days(self, **params):
        response = self.client.get(reverse("core:production_list"), params)
        self.response = response
        return response.context["days"]

    def test_entries_are_grouped_by_day_newest_first(self):
        days = self.days()
        self.assertEqual([d["date"] for d in days],
                         [date(2026, 6, 9), date(2026, 6, 5), date(2026, 6, 1)])

    def test_each_day_totals_its_products_and_quantity(self):
        days = {d["date"]: d for d in self.days()}
        first = days[date(2026, 6, 1)]
        self.assertEqual(first["product_count"], 2)
        self.assertEqual(first["total_qty"], Decimal("15.000"))

        last = days[date(2026, 6, 9)]
        self.assertEqual(last["product_count"], 1)
        self.assertEqual(last["total_qty"], Decimal("7.000"))

    def test_a_day_carries_its_entries_for_the_expanded_view(self):
        days = {d["date"]: d for d in self.days()}
        entries = days[date(2026, 6, 1)]["entries"]
        self.assertEqual({e.product.name for e in entries}, {"Pipe", "Tank"})

    def test_the_expanded_view_shows_the_snapshots(self):
        self.days()
        self.assertContains(self.response, "Stock Before")
        self.assertContains(self.response, "Stock After")

    def test_filter_by_date_range(self):
        days = self.days(from_date="2026-06-05", to_date="2026-06-09")
        self.assertEqual([d["date"] for d in days], [date(2026, 6, 9), date(2026, 6, 5)])

    def test_filter_by_product(self):
        days = self.days(product=self.pipe.pk)
        self.assertEqual([d["date"] for d in days], [date(2026, 6, 5), date(2026, 6, 1)])
        self.assertEqual(self.response.context["entry_count"], 2)

    def test_unknown_filter_values_are_ignored_not_500s(self):
        days = self.days(product="zzz", from_date="nonsense")
        self.assertEqual(len(days), 3)
        self.assertFalse(self.response.context["is_filtered"])

    def test_row_actions_are_offered(self):
        self.client.force_login(self.make_admin())
        response = self.client.get(reverse("core:production_list"))
        self.assertContains(response, reverse("core:production_edit", args=[self.jun1_pipe.pk]))
        self.assertContains(response, reverse("core:production_delete", args=[self.jun1_pipe.pk]))

    def test_manager_gets_no_delete_control(self):
        html = self.client.get(reverse("core:production_list")).content.decode()
        self.assertNotIn(reverse("core:production_delete", args=[self.jun1_pipe.pk]), html)
        self.assertNotIn('id="delete-modal"', html)

    def test_empty_state(self):
        ProductionEntry.objects.all().delete()
        response = self.client.get(reverse("core:production_list"))
        self.assertEqual(list(response.context["days"]), [])
        self.assertContains(response, "No production recorded yet.")


class SalesReportTests(UserFactoryMixin, TestCase):
    """Figures are hand-computed, so the report is checked against known
    answers rather than a re-run of its own aggregation.

    Nimal, 1 Jun: 1,000 bill, 1,000 cash kept in the drawer.
    Nimal, 5 Jun: 2,000 bill, 800 cash banked to Senovka + 1,200 cheque.
    Kamal, 9 Jun: 3,000 bill, pay later — nothing paid.
    Cancelled:    9,999, which must not reach a single figure.
    """

    @classmethod
    def setUpTestData(cls):
        cls.nimal = Customer.objects.create(name="Nimal")
        cls.kamal = Customer.objects.create(name="Kamal Traders")

        def bill(customer, day, total, paid, kind, status):
            return Bill.objects.create(
                customer=customer, bill_date=date(2026, 6, day),
                subtotal=Decimal(total), total_amount=Decimal(total),
                paid_amount=Decimal(paid),
                balance_change=Decimal(paid) - Decimal(total),
                payment_type=kind, status=status,
            )

        def payment(on, method, amount, account=""):
            return Payment.objects.create(
                bill=on, method=method, amount=Decimal(amount),
                account=account, paid_at=timezone.now(),
            )

        cls.jun1 = bill(cls.nimal, 1, "1000.00", "1000.00",
                        Bill.PaymentType.FULL_CASH, Bill.Status.PAID)
        payment(cls.jun1, Payment.Method.CASH, "1000.00")

        cls.jun5 = bill(cls.nimal, 5, "2000.00", "2000.00",
                        Bill.PaymentType.PARTIAL, Bill.Status.PAID)
        payment(cls.jun5, Payment.Method.CASH, "800.00", account="senovka")
        cheque_payment = payment(cls.jun5, Payment.Method.CHEQUE, "1200.00")
        cls.cheque = Cheque.objects.create(
            payment=cheque_payment, customer=cls.nimal, cheque_no="C-1001",
            bank_name="BOC", amount=Decimal("1200.00"),
            received_date=date(2026, 6, 5), maturity_date=date(2026, 7, 5),
        )

        cls.jun9 = bill(cls.kamal, 9, "3000.00", "0.00",
                        Bill.PaymentType.PAY_LATER, Bill.Status.UNPAID)

        cls.cancelled = bill(cls.nimal, 5, "9999.00", "9999.00",
                             Bill.PaymentType.FULL_CASH, Bill.Status.CANCELLED)
        payment(cls.cancelled, Payment.Method.CASH, "9999.00")

    def setUp(self):
        self.client.force_login(self.make_manager())

    def report(self, **params):
        response = self.client.get(reverse("core:sales_report"), params)
        self.response = response
        return response.context

    # ---- summary ----
    def test_the_cards_add_up(self):
        ctx = self.report()
        # 1,000 + 2,000 + 3,000
        self.assertEqual(ctx["total_sales"], Decimal("6000.00"))
        # 1,000 + 800
        self.assertEqual(ctx["total_cash"], Decimal("1800.00"))
        self.assertEqual(ctx["total_cheque"], Decimal("1200.00"))
        # Only the pay-later bill still owes.
        self.assertEqual(ctx["total_outstanding"], Decimal("3000.00"))

    def test_a_cancelled_bill_reaches_no_figure_at_all(self):
        """It isn't a sale, and its payment isn't money taken."""
        ctx = self.report()
        self.assertNotIn(self.cancelled.pk, [b.pk for b in ctx["bills"]])
        self.assertNotIn(Decimal("9999.00"), [r.amount for r in ctx["cash_rows"]])
        self.assertEqual(ctx["total_sales"], Decimal("6000.00"))
        self.assertEqual(ctx["total_cash"], Decimal("1800.00"))

    def test_outstanding_floors_at_zero_per_bill(self):
        """A payment that also cleared old debt can exceed its bill; that bill
        doesn't then owe a negative amount."""
        over = Bill.objects.create(
            customer=self.kamal, bill_date=date(2026, 6, 12),
            subtotal=Decimal("500.00"), total_amount=Decimal("500.00"),
            paid_amount=Decimal("4000.00"),
            balance_change=Decimal("3500.00"),
            payment_type=Bill.PaymentType.FULL_CASH, status=Bill.Status.PAID,
        )
        rows = {b.pk: b for b in self.report()["bills"]}
        self.assertEqual(rows[over.pk].outstanding, Decimal("0.00"))
        # 3,000 from the pay-later bill only.
        self.assertEqual(self.response.context["total_outstanding"], Decimal("3000.00"))

    # ---- cash section ----
    def test_cash_rows_carry_their_destination(self):
        rows = {r.amount: r for r in self.report()["cash_rows"]}
        self.assertEqual(rows[Decimal("1000.00")].account, "")
        self.assertEqual(rows[Decimal("800.00")].account, "senovka")

    def test_cash_subtotals_split_by_account(self):
        totals = dict(self.report()["account_totals"])
        self.assertEqual(totals["Physical"], Decimal("1000.00"))
        self.assertEqual(totals["Senovka Acc"], Decimal("800.00"))
        self.assertEqual(totals["Dinusha Acc"], Decimal("0.00"))

    def test_an_account_with_nothing_in_it_still_shows(self):
        """A zero subtotal is an answer; a missing row is a question."""
        labels = [label for label, _ in self.report()["account_totals"]]
        self.assertEqual(labels, ["Physical", "Senovka Acc", "Dinusha Acc"])

    def test_the_account_subtotals_sum_to_the_cash_card(self):
        ctx = self.report()
        self.assertEqual(
            sum(amount for _, amount in ctx["account_totals"]), ctx["total_cash"]
        )

    # ---- cheque section ----
    def test_cheque_rows_carry_the_cheque_details(self):
        rows = self.report()["cheque_rows"]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].cheque_no, "C-1001")
        self.assertEqual(rows[0].bank_name, "BOC")
        self.assertEqual(rows[0].maturity_date, date(2026, 7, 5))
        self.assertEqual(rows[0].status, Cheque.Status.PENDING)
        self.assertEqual(rows[0].payment.bill_id, self.jun5.pk)

    # ---- filters ----
    def test_filter_by_date_range(self):
        ctx = self.report(from_date="2026-06-05", to_date="2026-06-09")
        self.assertEqual({b.pk for b in ctx["bills"]}, {self.jun5.pk, self.jun9.pk})
        self.assertEqual(ctx["total_sales"], Decimal("5000.00"))
        # The 1 Jun cash falls outside, so it drops out of the card too.
        self.assertEqual(ctx["total_cash"], Decimal("800.00"))

    def test_filter_by_customer(self):
        ctx = self.report(customer_id=self.kamal.pk)
        self.assertEqual({b.pk for b in ctx["bills"]}, {self.jun9.pk})
        self.assertEqual(ctx["total_sales"], Decimal("3000.00"))
        self.assertEqual(ctx["total_cash"], Decimal("0.00"))
        self.assertEqual(ctx["total_outstanding"], Decimal("3000.00"))

    def test_filter_by_payment_type(self):
        ctx = self.report(payment_type="pay_later")
        self.assertEqual({b.pk for b in ctx["bills"]}, {self.jun9.pk})
        self.assertEqual(ctx["cheque_rows"], [])

    def test_filters_reach_the_payment_sections_too(self):
        """The cash and cheque tables are the same bills seen sideways, so a
        filter that moves one has to move the others."""
        ctx = self.report(customer_id=self.nimal.pk, payment_type="partial")
        self.assertEqual({b.pk for b in ctx["bills"]}, {self.jun5.pk})
        self.assertEqual([r.amount for r in ctx["cash_rows"]], [Decimal("800.00")])
        self.assertEqual(len(ctx["cheque_rows"]), 1)

    def test_filters_combine(self):
        ctx = self.report(from_date="2026-06-01", to_date="2026-06-05",
                          customer_id=self.nimal.pk)
        self.assertEqual({b.pk for b in ctx["bills"]}, {self.jun1.pk, self.jun5.pk})
        self.assertEqual(ctx["total_sales"], Decimal("3000.00"))

    def test_unknown_filter_values_are_ignored_not_500s(self):
        ctx = self.report(customer_id="zzz", payment_type="zzz", from_date="nonsense")
        self.assertEqual(len(ctx["bills"]), 3)
        self.assertFalse(ctx["is_filtered"])

    def test_an_empty_period_reports_zeroes_not_none(self):
        ctx = self.report(from_date="2027-01-01", to_date="2027-01-31")
        self.assertEqual(ctx["bills"], [])
        self.assertEqual(ctx["total_sales"], Decimal("0"))
        self.assertEqual(ctx["total_cash"], Decimal("0"))
        self.assertEqual(ctx["total_cheque"], Decimal("0"))
        self.assertEqual(ctx["total_outstanding"], Decimal("0"))
        self.assertContains(self.response, "No sales in this period.")

    # ---- the page ----
    def test_the_page_renders_all_three_tables(self):
        self.report()
        self.assertContains(self.response, "Cash Sales")
        self.assertContains(self.response, "Cheque Sales")
        self.assertContains(self.response, "All Sales")
        self.assertContains(self.response, "C-1001")
        self.assertContains(self.response, "6,000.00")

    def test_the_pdf_link_carries_the_current_filters(self):
        self.report(customer_id=self.nimal.pk, payment_type="partial")
        self.assertContains(self.response, reverse("core:sales_report_pdf"))
        self.assertContains(self.response, f"customer_id={self.nimal.pk}")

    def test_report_requires_login(self):
        self.client.logout()
        response = self.client.get(reverse("core:sales_report"))
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("login"), response["Location"])


class SalesReportPdfTests(UserFactoryMixin, TestCase):
    """The export. WeasyPrint may or may not be able to run here, so the test
    covers both answers rather than assume one."""

    @classmethod
    def setUpTestData(cls):
        cls.nimal = Customer.objects.create(name="Nimal")
        cls.bill = Bill.objects.create(
            customer=cls.nimal, bill_date=date(2026, 6, 1),
            subtotal=Decimal("1000.00"), total_amount=Decimal("1000.00"),
            paid_amount=Decimal("1000.00"), balance_change=Decimal("0.00"),
            payment_type=Bill.PaymentType.FULL_CASH, status=Bill.Status.PAID,
        )
        Payment.objects.create(
            bill=cls.bill, method=Payment.Method.CASH,
            amount=Decimal("1000.00"), paid_at=timezone.now(),
        )

    def setUp(self):
        self.client.force_login(self.make_manager())

    def weasyprint_works(self):
        try:
            import weasyprint  # noqa: F401
        except (ImportError, OSError):
            return False
        return True

    def test_export_answers_a_pdf_or_a_printable_page_never_a_500(self):
        response = self.client.get(reverse("core:sales_report_pdf"))
        self.assertEqual(response.status_code, 200)

        if self.weasyprint_works():
            self.assertEqual(response["Content-Type"], "application/pdf")
            self.assertTrue(response.content.startswith(b"%PDF"))
            self.assertIn("senovka-sales-", response["Content-Disposition"])
        else:
            # pip installs WeasyPrint fine on Windows, then importing it raises
            # OSError for the missing GTK libraries. The fallback hands back the
            # same document for the browser to print.
            self.assertEqual(response["Content-Type"], "text/html; charset=utf-8")
            self.assertContains(response, "Senovka Plastics")

    def test_the_pdf_template_stands_on_its_own(self):
        """WeasyPrint fetches nothing and runs no JavaScript, so the document
        cannot lean on the CDN or the app chrome."""
        html = render_to_string(
            "core/sales_report_pdf.html", _sales_report_context_for(self.client)
        )
        self.assertNotIn("cdn.tailwindcss.com", html)
        self.assertNotIn("<script", html)
        self.assertNotIn('id="sidebar"', html)
        self.assertIn("@page", html)

    def test_the_pdf_reports_the_same_figures_as_the_page(self):
        page = self.client.get(reverse("core:sales_report")).context
        pdf = self.client.get(reverse("core:sales_report_pdf"))
        # Whichever branch answered, the totals came from one function.
        self.assertEqual(page["total_sales"], Decimal("1000.00"))
        self.assertEqual(pdf.status_code, 200)

    def test_the_pdf_respects_the_filters(self):
        response = self.client.get(
            reverse("core:sales_report_pdf"), {"from_date": "2027-01-01"}
        )
        self.assertEqual(response.status_code, 200)
        if not self.weasyprint_works():
            self.assertContains(response, "No sales in this period.")

    def test_export_requires_login(self):
        self.client.logout()
        response = self.client.get(reverse("core:sales_report_pdf"))
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("login"), response["Location"])


def _sales_report_context_for(client):
    """The report context, as the view builds it, for template-only checks."""
    response = client.get(reverse("core:sales_report"))
    return {
        key: response.context[key]
        for key in (
            "bills", "cash_rows", "cheque_rows", "account_totals",
            "total_sales", "total_cash", "total_cheque", "total_outstanding",
            "from_date", "to_date", "selected_customer", "payment_type",
            "customers", "payment_types", "generated_at",
        )
    }


class LedgerPdfTests(UserFactoryMixin, TestCase):
    """The ledger as a document. Same rows as the page, so the two can't tell
    the customer different stories."""

    @classmethod
    def setUpTestData(cls):
        cls.nimal = Customer.objects.create(
            name="Nimal Stores", phone="077 123 4567", address="12 Galle Road",
            credit_limit=Decimal("10000.00"), balance=Decimal("-600.00"),
        )
        cat = Category.objects.create(name="Pipes")
        Product.objects.create(
            name="Pipe", category=cat, default_price=Decimal("100.00"), qty=Decimal("50.000")
        )

        def bill(day, total):
            return Bill.objects.create(
                customer=cls.nimal, bill_date=date(2026, 6, day),
                total_amount=Decimal(total),
                payment_type=Bill.PaymentType.PAY_LATER,
            )

        cls.june1 = bill(1, "1000.00")
        cls.june3 = bill(3, "500.00")
        Payment.objects.create(
            bill=cls.june3, method=Payment.Method.CASH, amount=Decimal("200.00"),
            paid_at=timezone.make_aware(datetime(2026, 6, 3, 14, 30)),
        )

    def setUp(self):
        self.client.force_login(self.make_manager())

    def url(self, **params):
        base = reverse("core:customer_ledger_pdf", args=[self.nimal.pk])
        return f"{base}?{urlencode(params)}" if params else base

    def weasyprint_works(self):
        try:
            import weasyprint  # noqa: F401
        except (ImportError, OSError):
            return False
        return True

    def test_export_answers_a_pdf_or_a_printable_page_never_a_500(self):
        response = self.client.get(self.url())
        self.assertEqual(response.status_code, 200)

        if self.weasyprint_works():
            self.assertEqual(response["Content-Type"], "application/pdf")
            self.assertTrue(response.content.startswith(b"%PDF"))
        else:
            # pip installs WeasyPrint on Windows and then importing it raises
            # OSError for the missing GTK libraries; the fallback hands back the
            # same document for the browser to print.
            self.assertEqual(response["Content-Type"], "text/html; charset=utf-8")
            self.assertContains(response, "Nimal Stores")

    def test_the_filename_carries_the_customer_and_the_date(self):
        response = self.client.get(self.url())
        stamp = timezone.localdate().isoformat()
        self.assertIn(
            f'filename="ledger_nimal-stores_{stamp}.pdf"',
            response["Content-Disposition"],
        )

    def test_a_name_that_would_break_the_header_is_slugified(self):
        """A customer name is free text; a raw one in a header is at best
        broken and at worst a way to inject a header."""
        awkward = Customer.objects.create(name='Bad "Name"; drop\r\nHeader')
        response = self.client.get(
            reverse("core:customer_ledger_pdf", args=[awkward.pk])
        )
        disposition = response["Content-Disposition"]
        self.assertNotIn("\n", disposition)
        self.assertNotIn('"Name"', disposition)
        self.assertIn("ledger_bad-name-drop-header_", disposition)

    def test_the_document_stands_on_its_own(self):
        """WeasyPrint fetches nothing and runs no JavaScript, so it can't lean
        on the CDN or the app chrome."""
        html = render_to_string("core/ledger_pdf.html", self.context())
        self.assertNotIn("cdn.tailwindcss.com", html)
        self.assertNotIn("<script", html)
        self.assertNotIn('id="sidebar"', html)
        self.assertIn("@page", html)

    def context(self, **params):
        """The context the view builds, for template-only checks."""
        request = self.client.get(self.url(**params)).wsgi_request
        customer = views._customers().get(pk=self.nimal.pk)
        rows = views._ledger_rows(
            customer,
            views._parse_date(params.get("from_date")),
            views._parse_date(params.get("to_date")),
        )
        return {
            "customer": customer,
            "rows": rows,
            "from_date": views._parse_date(params.get("from_date")),
            "to_date": views._parse_date(params.get("to_date")),
            "is_filtered": bool(params),
            "total_sale": sum((r["sale"] or Decimal("0") for r in rows), Decimal("0")),
            "total_credit": sum((r["credit"] or Decimal("0") for r in rows), Decimal("0")),
            "closing_balance": rows[-1]["balance"] if rows else Decimal("0"),
            "as_of": timezone.localdate(),
            "generated_at": timezone.localtime(),
        }

    def test_the_document_carries_the_header_and_summary(self):
        html = render_to_string("core/ledger_pdf.html", self.context())
        self.assertIn("Senovka Plastics", html)
        self.assertIn("Nimal Stores", html)
        self.assertIn("12 Galle Road", html)
        self.assertIn("077 123 4567", html)
        self.assertIn("Credit Limit", html)
        self.assertIn("Current Balance", html)
        self.assertIn("Available Credit", html)
        self.assertIn("10,000.00", html)   # credit limit
        self.assertIn("9,400.00", html)    # available: 10000 - 600 owed

    def test_the_running_balance_matches_the_page(self):
        """1,000 sale, 500 sale, 200 cash: 1,000 -> 1,500 -> 1,300."""
        context = self.context()
        self.assertEqual(
            [r["balance"] for r in context["rows"]],
            [Decimal("1000.00"), Decimal("1500.00"), Decimal("1300.00")],
        )
        self.assertEqual(context["closing_balance"], Decimal("1300.00"))

        page = self.client.get(
            reverse("core:customer_ledger", args=[self.nimal.pk])
        ).context
        self.assertEqual(
            [r["balance"] for r in page["rows"]],
            [r["balance"] for r in context["rows"]],
        )

    def test_the_footer_states_the_closing_balance(self):
        html = render_to_string("core/ledger_pdf.html", self.context())
        self.assertIn("Closing Balance as of", html)
        self.assertIn("1,300.00", html)

    def test_the_range_reaches_the_document(self):
        response = self.client.get(self.url(from_date="2026-06-03"))
        if not self.weasyprint_works():
            self.assertContains(response, "3 Jun 2026")
            self.assertNotContains(response, "1 Jun 2026")

    def test_export_requires_login(self):
        self.client.logout()
        response = self.client.get(self.url())
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("login"), response["Location"])

    def test_export_of_a_missing_customer_404s(self):
        self.assertEqual(
            self.client.get(reverse("core:customer_ledger_pdf", args=[9999])).status_code,
            404,
        )


class OutstandingReportTests(UserFactoryMixin, TestCase):
    """Figures are hand-computed.

    Big Debtor:   billed 12,000, received 2,000  -> owes 10,000
    Small Debtor: billed  1,000, received     0  -> owes  1,000
    Settled:      billed  5,000, received 5,000  -> square
    In Credit:    we owe them 2,000
    Never:        no activity at all
    """

    @classmethod
    def setUpTestData(cls):
        cat = Category.objects.create(name="Pipes")
        Product.objects.create(
            name="Pipe", category=cat, default_price=Decimal("100.00"), qty=Decimal("99.000")
        )

        def account(name, balance, limit="20000.00", **kwargs):
            return Customer.objects.create(
                name=name, balance=Decimal(balance),
                credit_limit=Decimal(limit), **kwargs
            )

        cls.big = account("Big Debtor", "-10000.00", phone="077 111")
        cls.small = account("Small Debtor", "-1000.00")
        cls.settled = account("Settled Co", "0.00")
        cls.credit = account("In Credit", "2000.00")
        cls.never = account("Never Traded", "0.00")

        def bill(customer, day, total, paid, status=Bill.Status.UNPAID):
            return Bill.objects.create(
                customer=customer, bill_date=date(2026, 6, day),
                subtotal=Decimal(total), total_amount=Decimal(total),
                paid_amount=Decimal(paid),
                balance_change=Decimal(paid) - Decimal(total),
                payment_type=Bill.PaymentType.PARTIAL, status=status,
            )

        big_bill = bill(cls.big, 1, "12000.00", "2000.00")
        Payment.objects.create(
            bill=big_bill, method=Payment.Method.CASH, amount=Decimal("2000.00"),
            paid_at=timezone.make_aware(datetime(2026, 6, 4, 9, 0)),
        )
        bill(cls.small, 2, "1000.00", "0.00")
        settled_bill = bill(cls.settled, 3, "5000.00", "5000.00", Bill.Status.PAID)
        Payment.objects.create(
            bill=settled_bill, method=Payment.Method.CASH, amount=Decimal("5000.00"),
            paid_at=timezone.make_aware(datetime(2026, 6, 3, 9, 0)),
        )

        # Must not reach a single figure.
        cancelled = bill(cls.big, 7, "9999.00", "9999.00", Bill.Status.CANCELLED)
        Payment.objects.create(
            bill=cancelled, method=Payment.Method.CASH, amount=Decimal("9999.00"),
            paid_at=timezone.make_aware(datetime(2026, 6, 7, 9, 0)),
        )

    def setUp(self):
        self.client.force_login(self.make_manager())

    def rows(self, **params):
        response = self.client.get(reverse("core:outstanding_report"), params)
        self.response = response
        return {c.name: c for c in response.context["customers"]}

    # ---- scope ----
    def test_the_default_is_only_those_who_owe(self):
        rows = self.rows()
        self.assertEqual(set(rows), {"Big Debtor", "Small Debtor"})
        self.assertEqual(self.response.context["scope"], "owing")

    def test_all_customers_can_be_shown(self):
        rows = self.rows(scope="all")
        self.assertEqual(
            set(rows),
            {"Big Debtor", "Small Debtor", "Settled Co", "In Credit", "Never Traded"},
        )

    def test_an_unknown_scope_falls_back_to_owing(self):
        rows = self.rows(scope="zzz")
        self.assertEqual(set(rows), {"Big Debtor", "Small Debtor"})

    # ---- ordering ----
    def test_sorted_by_the_largest_debt_first(self):
        response = self.client.get(reverse("core:outstanding_report"), {"scope": "all"})
        names = [c.name for c in response.context["customers"]]
        self.assertEqual(names[:2], ["Big Debtor", "Small Debtor"])
        # Everyone who owes nothing sits at 0 and falls in behind, by name.
        self.assertEqual(names[2:], ["In Credit", "Never Traded", "Settled Co"])

    # ---- the figures ----
    def test_billed_and_received_are_totalled_per_customer(self):
        rows = self.rows()
        self.assertEqual(rows["Big Debtor"].total_billed, Decimal("12000.00"))
        self.assertEqual(rows["Big Debtor"].total_received, Decimal("2000.00"))
        self.assertEqual(rows["Small Debtor"].total_billed, Decimal("1000.00"))
        self.assertEqual(rows["Small Debtor"].total_received, Decimal("0.00"))

    def test_a_cancelled_bill_reaches_no_figure(self):
        rows = self.rows()
        # 12,000 not 21,999, and 2,000 not 11,999.
        self.assertEqual(rows["Big Debtor"].total_billed, Decimal("12000.00"))
        self.assertEqual(rows["Big Debtor"].total_received, Decimal("2000.00"))

    def test_totals_are_not_inflated_by_the_join(self):
        """Summing bills and payments in one query would count each bill once
        per payment on it. A second payment must not double the billed figure."""
        extra = Bill.objects.get(customer=self.big, bill_date=date(2026, 6, 1))
        for _ in range(3):
            Payment.objects.create(
                bill=extra, method=Payment.Method.CASH, amount=Decimal("1.00"),
                paid_at=timezone.now(),
            )
        rows = self.rows()
        self.assertEqual(rows["Big Debtor"].total_billed, Decimal("12000.00"))
        self.assertEqual(rows["Big Debtor"].total_received, Decimal("2003.00"))

    def test_a_bounced_cheque_is_not_received(self):
        """Same rule as the ledger, so the two reports agree."""
        bill = Bill.objects.get(customer=self.small)
        payment = Payment.objects.create(
            bill=bill, method=Payment.Method.CHEQUE, amount=Decimal("400.00"),
            paid_at=timezone.now(),
        )
        Cheque.objects.create(
            payment=payment, customer=self.small, cheque_no="C-1", bank_name="BOC",
            amount=Decimal("400.00"), received_date=date(2026, 6, 2),
            maturity_date=date(2026, 7, 2), status=Cheque.Status.BOUNCED,
        )
        self.assertEqual(self.rows()["Small Debtor"].total_received, Decimal("0.00"))

    def test_credit_figures_come_through(self):
        rows = self.rows()
        big = rows["Big Debtor"]
        self.assertEqual(big.owed, Decimal("10000.00"))
        self.assertEqual(big.credit_limit, Decimal("20000.00"))
        self.assertEqual(big.available_credit, Decimal("10000.00"))

    def test_a_customer_in_credit_has_their_whole_limit(self):
        rows = self.rows(scope="all")
        self.assertEqual(rows["In Credit"].owed, Decimal("0.00"))
        self.assertEqual(rows["In Credit"].available_credit, Decimal("20000.00"))

    def test_last_transaction_is_the_later_of_a_bill_or_a_payment(self):
        """Billed 1 Jun, paid 4 Jun: the account was last touched on the 4th."""
        self.assertEqual(self.rows()["Big Debtor"].last_transaction, date(2026, 6, 4))

    def test_last_transaction_falls_back_to_the_bill(self):
        self.assertEqual(self.rows()["Small Debtor"].last_transaction, date(2026, 6, 2))

    def test_a_customer_who_never_traded_has_no_last_transaction(self):
        self.assertIsNone(self.rows(scope="all")["Never Traded"].last_transaction)

    def test_the_summary_totals_the_rows(self):
        self.rows()
        ctx = self.response.context
        self.assertEqual(ctx["total_owed"], Decimal("11000.00"))
        self.assertEqual(ctx["total_billed"], Decimal("13000.00"))
        self.assertEqual(ctx["total_received"], Decimal("2000.00"))

    # ---- the page ----
    def test_the_page_renders_every_column(self):
        self.rows()
        for heading in (
            "Customer", "Phone", "Total Billed", "Total Received",
            "Current Balance", "Credit Limit", "Available Credit", "Last Transaction",
        ):
            with self.subTest(heading=heading):
                self.assertContains(self.response, heading)
        self.assertContains(self.response, "077 111")

    def test_the_page_links_to_each_ledger(self):
        self.rows()
        self.assertContains(self.response, reverse("core:customer_ledger", args=[self.big.pk]))
        self.assertContains(self.response, reverse("core:customer_ledger_pdf", args=[self.big.pk]))

    def test_the_pdf_link_carries_the_scope(self):
        self.rows(scope="all")
        self.assertContains(self.response, reverse("core:outstanding_report_pdf"))
        self.assertContains(self.response, "scope=all")

    def test_a_manager_sees_the_same_as_a_super_admin(self):
        """Explicitly the same report for both roles."""
        manager_rows = set(self.rows(scope="all"))
        self.client.force_login(self.make_admin())
        admin_rows = set(self.rows(scope="all"))
        self.assertEqual(manager_rows, admin_rows)

    def test_the_report_requires_login(self):
        self.client.logout()
        response = self.client.get(reverse("core:outstanding_report"))
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("login"), response["Location"])

    # ---- the export ----
    def test_export_answers_a_pdf_or_a_printable_page_never_a_500(self):
        response = self.client.get(reverse("core:outstanding_report_pdf"))
        self.assertEqual(response.status_code, 200)
        try:
            import weasyprint  # noqa: F401
        except (ImportError, OSError):
            self.assertEqual(response["Content-Type"], "text/html; charset=utf-8")
            self.assertContains(response, "Big Debtor")
        else:
            self.assertEqual(response["Content-Type"], "application/pdf")
            self.assertTrue(response.content.startswith(b"%PDF"))
            self.assertIn("senovka-outstanding-", response["Content-Disposition"])

    def test_the_export_respects_the_scope(self):
        response = self.client.get(reverse("core:outstanding_report_pdf"), {"scope": "all"})
        self.assertEqual(response.status_code, 200)
        try:
            import weasyprint  # noqa: F401
        except (ImportError, OSError):
            self.assertContains(response, "Never Traded")

    def test_the_export_document_stands_on_its_own(self):
        html = render_to_string(
            "core/outstanding_pdf.html",
            {
                "customers": [], "scope": "owing",
                "total_owed": Decimal("0"), "total_billed": Decimal("0"),
                "total_received": Decimal("0"),
                "generated_at": timezone.localtime(),
            },
        )
        self.assertNotIn("cdn.tailwindcss.com", html)
        self.assertNotIn("<script", html)
        self.assertNotIn('id="sidebar"', html)
        self.assertIn("@page", html)

    def test_export_requires_login(self):
        self.client.logout()
        response = self.client.get(reverse("core:outstanding_report_pdf"))
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("login"), response["Location"])


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


class UserManagementTests(UserFactoryMixin, TestCase):
    """Accounts are created here or nowhere: there is no self-registration, and
    only a super admin may open any of these pages."""

    def setUp(self):
        self.admin = self.make_admin()
        self.client.force_login(self.admin)

    def create(self, **overrides):
        data = {
            "username": "nimal",
            "first_name": "Nimal",
            "last_name": "Perera",
            "email": "nimal@senovka.lk",
            "role": User.Role.MANAGER,
            "is_active": "on",
            **overrides,
        }
        return self.client.post(reverse("core:user_create"), data)

    # ---- access ----
    def test_every_user_url_is_closed_to_managers(self):
        target = self.make_manager()
        self.client.force_login(target)

        pages = [
            ("core:user_list", "get"),
            ("core:user_create", "get"),
            ("core:user_edit", "get"),
            ("core:user_reset_password", "post"),
            ("core:user_deactivate", "post"),
            ("core:user_activate", "post"),
        ]
        for name, method in pages:
            with self.subTest(url=name):
                args = [] if name in {"core:user_list", "core:user_create"} else [self.admin.pk]
                response = getattr(self.client, method)(reverse(name, args=args))
                self.assertRedirects(response, reverse("core:dashboard"))

        # Not merely hidden — the manager reached none of it.
        self.admin.refresh_from_db()
        self.assertTrue(self.admin.is_active)

    # ---- creating ----
    def test_creating_a_user_stores_the_fields(self):
        response = self.create()
        self.assertRedirects(response, reverse("core:user_list"))

        user = User.objects.get(username="nimal")
        self.assertEqual(user.first_name, "Nimal")
        self.assertEqual(user.email, "nimal@senovka.lk")
        self.assertEqual(user.role, User.Role.MANAGER)
        self.assertTrue(user.is_active)

    def test_the_generated_password_works_and_is_hashed(self):
        self.create()
        user = User.objects.get(username="nimal")

        password = self.client.session["new_credentials"]["password"]
        self.assertEqual(len(password), 8)
        # Stored as a hash, never as the text that was shown.
        self.assertNotEqual(user.password, password)
        self.assertTrue(user.check_password(password))

    def test_the_password_is_shown_once_and_then_gone(self):
        self.create()
        first = self.client.get(reverse("core:user_list"))
        self.assertIsNotNone(first.context["credentials"])
        self.assertEqual(first.context["credentials"]["username"], "nimal")

        # Coming back to the list must not put it on screen again.
        second = self.client.get(reverse("core:user_list"))
        self.assertIsNone(second.context["credentials"])

    def test_a_duplicate_username_is_refused_whatever_its_case(self):
        self.create()
        response = self.create(username="NIMAL")
        self.assertFormError(response.context["form"], "username", "That username is taken.")
        self.assertEqual(User.objects.filter(username__iexact="nimal").count(), 1)

    def test_a_username_is_stored_lower_cased(self):
        self.create(username="Nimal")
        self.assertTrue(User.objects.filter(username="nimal").exists())

    def test_a_super_admin_can_be_created(self):
        self.create(username="dinusha", role=User.Role.SUPER_ADMIN)
        self.assertEqual(
            User.objects.get(username="dinusha").role, User.Role.SUPER_ADMIN
        )

    # ---- editing ----
    def test_editing_updates_the_fields(self):
        self.create()
        user = User.objects.get(username="nimal")

        self.client.post(
            reverse("core:user_edit", args=[user.pk]),
            {
                "first_name": "Nimal",
                "last_name": "Silva",
                "email": "n.silva@senovka.lk",
                "role": User.Role.SUPER_ADMIN,
                "is_active": "on",
            },
        )
        user.refresh_from_db()
        self.assertEqual(user.last_name, "Silva")
        self.assertEqual(user.role, User.Role.SUPER_ADMIN)

    def test_the_username_cannot_be_changed(self):
        self.create()
        user = User.objects.get(username="nimal")

        self.client.post(
            reverse("core:user_edit", args=[user.pk]),
            {
                "username": "someone_else",
                "first_name": "Nimal",
                "last_name": "Perera",
                "email": "",
                "role": User.Role.MANAGER,
                "is_active": "on",
            },
        )
        user.refresh_from_db()
        self.assertEqual(user.username, "nimal")

    def test_a_super_admin_cannot_change_their_own_role(self):
        """The field is dropped from the form, so the POST can't reach the
        column — hiding it in the template would not be enough."""
        self.client.post(
            reverse("core:user_edit", args=[self.admin.pk]),
            {
                "first_name": "Boss",
                "last_name": "",
                "email": "",
                "role": User.Role.MANAGER,
                "is_active": "on",
            },
        )
        self.admin.refresh_from_db()
        self.assertEqual(self.admin.role, User.Role.SUPER_ADMIN)
        # The rest of the edit still went through.
        self.assertEqual(self.admin.first_name, "Boss")

    def test_a_super_admin_cannot_deactivate_themselves_through_the_form(self):
        self.client.post(
            reverse("core:user_edit", args=[self.admin.pk]),
            {"first_name": "", "last_name": "", "email": "", "is_active": ""},
        )
        self.admin.refresh_from_db()
        self.assertTrue(self.admin.is_active)

    # ---- deactivating ----
    def test_deactivating_stops_them_signing_in(self):
        self.create()
        user = User.objects.get(username="nimal")
        password = self.client.session["new_credentials"]["password"]

        self.client.post(reverse("core:user_deactivate", args=[user.pk]))
        user.refresh_from_db()
        self.assertFalse(user.is_active)

        # The account is closed, not just flagged.
        self.client.logout()
        response = self.client.post(
            reverse("login"), {"username": "nimal", "password": password}
        )
        self.assertEqual(response.status_code, 200)  # back on the form, not in

    def test_deactivating_leaves_their_records_alone(self):
        self.create()
        user = User.objects.get(username="nimal")
        customer = Customer.objects.create(name="Acme")
        bill = Bill.objects.create(
            customer=customer,
            bill_date=timezone.localdate(),
            total_amount=Decimal("100.00"),
            payment_type=Bill.PaymentType.PAY_LATER,
        )
        BillEditAudit.objects.create(
            bill=bill,
            edit_date=timezone.localdate(),
            reason="Wrong qty entered",
            created_by=user,
        )

        self.client.post(reverse("core:user_deactivate", args=[user.pk]))

        self.assertTrue(Bill.objects.filter(pk=bill.pk).exists())
        self.assertEqual(BillEditAudit.objects.get().created_by, user)

    def test_a_super_admin_cannot_deactivate_their_own_account(self):
        response = self.client.post(
            reverse("core:user_deactivate", args=[self.admin.pk])
        )
        self.assertRedirects(response, reverse("core:user_list"))
        self.admin.refresh_from_db()
        self.assertTrue(self.admin.is_active)

    def test_activating_lets_them_back_in(self):
        self.create(is_active="")
        user = User.objects.get(username="nimal")
        self.assertFalse(user.is_active)

        self.client.post(reverse("core:user_activate", args=[user.pk]))
        user.refresh_from_db()
        self.assertTrue(user.is_active)

    def test_deactivate_and_activate_refuse_a_get(self):
        self.create()
        user = User.objects.get(username="nimal")
        for name in ["core:user_deactivate", "core:user_activate"]:
            with self.subTest(url=name):
                response = self.client.get(reverse(name, args=[user.pk]))
                self.assertEqual(response.status_code, 405)

    # ---- resetting a password ----
    def test_resetting_replaces_the_password(self):
        self.create()
        user = User.objects.get(username="nimal")
        first = self.client.session["new_credentials"]["password"]

        self.client.post(reverse("core:user_reset_password", args=[user.pk]))
        second = self.client.session["new_credentials"]["password"]

        self.assertNotEqual(first, second)
        user.refresh_from_db()
        self.assertTrue(user.check_password(second))
        self.assertFalse(user.check_password(first))

    def test_resetting_shows_the_new_password_once(self):
        self.create()
        user = User.objects.get(username="nimal")
        self.client.get(reverse("core:user_list"))  # clear the create's password

        self.client.post(reverse("core:user_reset_password", args=[user.pk]))
        response = self.client.get(reverse("core:user_list"))
        self.assertEqual(response.context["credentials"]["username"], "nimal")
        self.assertIsNone(
            self.client.get(reverse("core:user_list")).context["credentials"]
        )

    def test_resetting_your_own_password_does_not_sign_you_out(self):
        """Changing a password rotates the session auth hash; without
        update_session_auth_hash the reset would log the admin out."""
        response = self.client.post(
            reverse("core:user_reset_password", args=[self.admin.pk])
        )
        self.assertRedirects(response, reverse("core:user_list"))
        # Still signed in: the list renders rather than bouncing to login.
        self.assertEqual(
            self.client.get(reverse("core:user_list")).status_code, 200
        )

    def test_reset_refuses_a_get(self):
        response = self.client.get(
            reverse("core:user_reset_password", args=[self.admin.pk])
        )
        self.assertEqual(response.status_code, 405)

    def test_generated_passwords_avoid_ambiguous_characters(self):
        from core.models import generate_password

        seen = "".join(generate_password() for _ in range(200))
        for char in "Il1O0":
            self.assertNotIn(char, seen)
