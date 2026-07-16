"""Throwaway verification for the pagination work. Deleted after it runs."""
from datetime import date, timedelta
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse

from .models import (
    Bill,
    CashDrawer,
    Category,
    Customer,
    Payment,
    Product,
    ProductionEntry,
    User,
)


class PaginationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(
            "admin", password="x", role=User.Role.SUPER_ADMIN
        )
        cls.cat = Category.objects.create(name="Pipes")
        cls.customer = Customer.objects.create(name="Nimal Stores")

        # 60 of everything: enough for 3 pages at 25 and 2 pages at 50.
        for i in range(60):
            Product.objects.create(name=f"P{i:03d}", category=cls.cat)
            Customer.objects.create(name=f"C{i:03d}")
            Category.objects.create(name=f"Cat{i:03d}")
            Bill.objects.create(
                customer=cls.customer,
                bill_date=date(2026, 1, 1) + timedelta(days=i),
                subtotal=Decimal("100.00"),
                total_amount=Decimal("100.00"),
                paid_amount=Decimal("0.00"),
                balance_change=Decimal("-100.00"),
                payment_type=Bill.PaymentType.PAY_LATER,
            )
            CashDrawer.objects.create(
                txn_date=date(2026, 1, 1) + timedelta(days=i),
                txn_type=CashDrawer.TxnType.IN,
                amount=Decimal("10.00"),
                reason=f"in {i}",
            )
            ProductionEntry.objects.create(
                product=Product.objects.first() or Product.objects.create(
                    name="X", category=cls.cat
                ),
                production_date=date(2026, 1, 1) + timedelta(days=i),
                qty_produced=Decimal("1.000"),
                reason="test",
            )

    def setUp(self):
        self.client.force_login(self.user)

    def test_list_pages_are_25(self):
        for name in [
            "core:bill_list",
            "core:customer_list",
            "core:product_list",
            "core:category_list",
            "core:supplier_bill_list",
            "core:cheque_list",
            "core:production_list",
            "core:cash_drawer",
        ]:
            with self.subTest(name=name):
                r = self.client.get(reverse(name))
                page = r.context["page_obj"]
                self.assertEqual(page.paginator.per_page, 25, name)

    def test_report_pages_are_50(self):
        for url in [
            reverse("core:customer_ledger", args=[self.customer.pk]),
            reverse("core:sales_report"),
            reverse("core:outstanding_report"),
        ]:
            with self.subTest(url=url):
                r = self.client.get(url)
                self.assertEqual(r.context["page_obj"].paginator.per_page, 50, url)

    def test_bill_list_pages_and_counts(self):
        r = self.client.get(reverse("core:bill_list"))
        page = r.context["page_obj"]
        self.assertEqual(page.paginator.count, 60)
        self.assertEqual(page.paginator.num_pages, 3)
        self.assertEqual(len(r.context["bills"]), 25)
        self.assertContains(r, "Showing")
        self.assertContains(r, "to <span")

        r2 = self.client.get(reverse("core:bill_list"), {"page": 3})
        self.assertEqual(len(r2.context["bills"]), 10)
        self.assertEqual(r2.context["page_obj"].start_index(), 51)
        self.assertEqual(r2.context["page_obj"].end_index(), 60)

    def test_filters_survive_paging(self):
        """The pager must carry the filters, not drop them."""
        r = self.client.get(
            reverse("core:bill_list"),
            {"from_date": "2026-01-01", "to_date": "2026-12-31", "status": "unpaid"},
        )
        html = r.content.decode()
        # The next-page link keeps every filter alongside page=2.
        self.assertIn("page=2", html)
        self.assertIn("from_date=2026-01-01", html)
        self.assertIn("to_date=2026-12-31", html)
        self.assertIn("status=unpaid", html)

    def test_page_param_is_not_duplicated(self):
        """?page=2 -> next must be page=3, not page=2&page=3."""
        r = self.client.get(reverse("core:bill_list"), {"page": 2, "status": "unpaid"})
        html = r.content.decode()
        self.assertNotIn("page=2&amp;page=3", html)
        self.assertIn("page=3", html)

    def test_bad_page_falls_back(self):
        for bad in ["abc", "0", "999"]:
            with self.subTest(bad=bad):
                r = self.client.get(reverse("core:bill_list"), {"page": bad})
                self.assertEqual(r.status_code, 200)

    def test_ellipsis_and_current_page(self):
        r = self.client.get(reverse("core:bill_list"), {"page": 2})
        html = r.content.decode()
        self.assertIn('aria-current="page"', html)
        self.assertIn("Previous", html)
        self.assertIn("Next", html)

    def test_cash_drawer_running_balance_continues_across_pages(self):
        """Page 2's running column must carry page 1's rows, not restart."""
        r1 = self.client.get(reverse("core:cash_drawer"))
        r2 = self.client.get(reverse("core:cash_drawer"), {"page": 2})
        last_p1 = r1.context["rows"][-1]["running"]
        first_p2 = r2.context["rows"][0]["running"]
        self.assertEqual(first_p2, last_p1 + Decimal("10.00"))
        # Totals describe the whole range, not the page.
        self.assertEqual(r2.context["total_in"], Decimal("600.00"))

    def test_production_is_paged_by_day(self):
        r = self.client.get(reverse("core:production_list"))
        page = r.context["page_obj"]
        self.assertEqual(page.paginator.count, 60)  # 60 distinct days
        self.assertEqual(len(r.context["days"]), 25)

    def test_pages_do_not_overlap_or_drop_rows(self):
        """Every row appears exactly once across the pages.

        The annotated list querysets group, which drops Meta.ordering and
        leaves no ORDER BY; LIMIT/OFFSET over that is free to repeat a row on
        two pages and never show another.
        """
        cases = [
            ("core:bill_list", "bills", 60),
            ("core:customer_list", "customers", 61),
            ("core:product_list", "products", 60),
            ("core:category_list", "categories", 61),
        ]
        for name, key, expected in cases:
            with self.subTest(name=name):
                seen = []
                r = self.client.get(reverse(name))
                pages = r.context["page_obj"].paginator.num_pages
                for n in range(1, pages + 1):
                    resp = self.client.get(reverse(name), {"page": n})
                    seen += [obj.pk for obj in resp.context[key]]
                self.assertEqual(len(seen), expected, f"{name}: row count")
                self.assertEqual(
                    len(set(seen)), expected, f"{name}: a row appeared twice"
                )

    def test_list_querysets_are_ordered(self):
        """Paginator warns on an unordered list; none should be unordered."""
        import warnings

        for name in [
            "core:bill_list",
            "core:customer_list",
            "core:product_list",
            "core:category_list",
            "core:supplier_bill_list",
        ]:
            with self.subTest(name=name):
                with warnings.catch_warnings(record=True) as caught:
                    warnings.simplefilter("always")
                    self.client.get(reverse(name))
                unordered = [
                    w for w in caught if "UnorderedObjectList" in type(w.message).__name__
                ]
                self.assertEqual(unordered, [], f"{name} paginates an unordered list")

    def test_pdf_exports_are_not_paginated(self):
        """The reports' PDFs must still carry every row."""
        r = self.client.get(reverse("core:sales_report_pdf"))
        self.assertEqual(r.status_code, 200)
        r2 = self.client.get(reverse("core:outstanding_report_pdf"))
        self.assertEqual(r2.status_code, 200)
