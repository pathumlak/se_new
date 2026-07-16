"""The bill form's backend: totals, bill date, walk-ins, and the product feed.

The card UI collects delivery, discount, a bill date and a walk-in name; these
cover the server side of each, because none of the page's own validation is
evidence — _write_bill re-derives every figure.
"""
import json
import re
from collections import Counter
from datetime import date, timedelta
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from .models import Bill, Category, Customer, Product, User


class BillFormBackendTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(
            "admin", password="x", role=User.Role.SUPER_ADMIN
        )
        cls.cat = Category.objects.create(name="Pipes")
        cls.other = Category.objects.create(name="Tanks")
        cls.customer = Customer.objects.create(
            name="Nimal Stores", credit_limit=Decimal("100000.00")
        )
        cls.p1 = Product.objects.create(
            name="Pipe A", size="2in", category=cls.cat,
            qty=Decimal("100.000"), default_price=Decimal("100.00"),
        )
        cls.dead = Product.objects.create(
            name="Pipe Z", size="9in", category=cls.other,
            qty=Decimal("0.000"), default_price=Decimal("50.00"),
        )

    def setUp(self):
        self.client.force_login(self.user)

    def payload(self, **over):
        body = {
            "customer_id": self.customer.pk,
            "lines": [{"product_id": self.p1.pk, "qty": "2", "unit_price": "100.00"}],
            "payment": {"type": "pay_later"},
        }
        body.update(over)
        return body

    def save(self, body):
        return self.client.post(
            reverse("core:bill_save"),
            data=json.dumps(body),
            content_type="application/json",
        )

    def walk_in(self, name="Sunil", **over):
        """A walk-in must be paid now, so these all settle in cash."""
        body = self.payload(
            is_walk_in=True,
            walk_in_name=name,
            payment={"type": "full_cash", "cash": "200.00"},
        )
        body.update(over)
        return body

    # ---- the product feed ----

    def test_endpoint_returns_category_and_out_of_stock(self):
        """The grid draws out-of-stock products as dimmed cards, so it has to
        be told about them."""
        r = self.client.get(reverse("core:bill_products", args=[self.customer.pk]))
        rows = {p["name"]: p for p in r.json()}
        self.assertIn("Pipe Z", rows)
        self.assertEqual(rows["Pipe Z"]["qty"], "0")
        self.assertEqual(rows["Pipe A"]["category"], "Pipes")
        self.assertEqual(rows["Pipe A"]["category_id"], self.cat.pk)

    def test_endpoint_still_hides_inactive(self):
        Product.objects.create(
            name="Gone", category=self.cat, qty=Decimal("5"), is_active=False
        )
        r = self.client.get(reverse("core:bill_products", args=[self.customer.pk]))
        self.assertNotIn("Gone", [p["name"] for p in r.json()])

    def test_out_of_stock_cannot_actually_be_billed(self):
        """The card is unclickable, but the page is not the guard."""
        r = self.save(self.payload(
            lines=[{"product_id": self.dead.pk, "qty": "1", "unit_price": "50.00"}]
        ))
        self.assertEqual(r.status_code, 400)

    # ---- totals ----

    def test_delivery_and_discount_reach_the_total(self):
        r = self.save(self.payload(
            delivery_charge="50.00",
            discount_amount="30.00",
            discount_reason="Damaged box",
        ))
        self.assertEqual(r.status_code, 200, r.content)
        bill = Bill.objects.get(pk=r.json()["bill_id"])
        self.assertEqual(bill.subtotal, Decimal("200.00"))
        self.assertEqual(bill.delivery_charge, Decimal("50.00"))
        self.assertEqual(bill.discount_amount, Decimal("30.00"))
        self.assertEqual(bill.discount_reason, "Damaged box")
        self.assertEqual(bill.total_amount, Decimal("220.00"))
        self.assertEqual(bill.balance_change, Decimal("-220.00"))
        self.customer.refresh_from_db()
        self.assertEqual(self.customer.balance, Decimal("-220.00"))

    def test_payment_collects_the_total_not_the_subtotal(self):
        r = self.save(self.payload(
            delivery_charge="50.00",
            discount_amount="30.00",
            discount_reason="Damaged box",
            payment={"type": "full_cash", "cash": "200.00"},
        ))
        self.assertEqual(r.status_code, 400)
        self.assertIn("220.00", r.json()["error"])

        r2 = self.save(self.payload(
            delivery_charge="50.00",
            discount_amount="30.00",
            discount_reason="Damaged box",
            payment={"type": "full_cash", "cash": "220.00"},
        ))
        self.assertEqual(r2.status_code, 200, r2.content)
        self.assertEqual(
            Bill.objects.get(pk=r2.json()["bill_id"]).status, Bill.Status.PAID
        )

    def test_credit_limit_measures_the_total(self):
        """Delivery pushes the debt up; the limit has to see it."""
        tight = Customer.objects.create(name="Tight", credit_limit=Decimal("210.00"))
        r = self.save(self.payload(customer_id=tight.pk, delivery_charge="50.00"))
        self.assertEqual(r.status_code, 400)
        self.assertIn("credit limit", r.json()["error"])

    def test_discount_needs_a_reason(self):
        r = self.save(self.payload(discount_amount="30.00"))
        self.assertEqual(r.status_code, 400)
        self.assertIn("reason", r.json()["error"].lower())

    def test_discount_cannot_exceed_the_bill(self):
        r = self.save(self.payload(discount_amount="9999.00", discount_reason="oops"))
        self.assertEqual(r.status_code, 400)
        self.assertIn("more than the bill", r.json()["error"])

    def test_omitting_the_new_fields_prices_as_before(self):
        r = self.save(self.payload())
        bill = Bill.objects.get(pk=r.json()["bill_id"])
        self.assertEqual(bill.total_amount, Decimal("200.00"))
        self.assertEqual(bill.delivery_charge, Decimal("0"))
        self.assertEqual(bill.discount_amount, Decimal("0"))

    # ---- bill date ----

    def test_bill_date_is_honoured(self):
        r = self.save(self.payload(bill_date="2026-03-04"))
        self.assertEqual(
            Bill.objects.get(pk=r.json()["bill_id"]).bill_date, date(2026, 3, 4)
        )

    def test_blank_bill_date_is_today(self):
        r = self.save(self.payload(bill_date=""))
        self.assertEqual(
            Bill.objects.get(pk=r.json()["bill_id"]).bill_date, timezone.localdate()
        )

    def test_future_bill_date_is_refused(self):
        ahead = (timezone.localdate() + timedelta(days=1)).isoformat()
        r = self.save(self.payload(bill_date=ahead))
        self.assertEqual(r.status_code, 400)
        self.assertIn("future", r.json()["error"])

    def test_nonsense_bill_date_is_refused(self):
        self.assertEqual(self.save(self.payload(bill_date="2026-02-31")).status_code, 400)

    def test_edit_keeps_the_original_bill_date(self):
        pk = self.save(self.payload(bill_date="2026-03-04")).json()["bill_id"]
        r = self.client.post(
            reverse("core:bill_edit", args=[pk]),
            data=json.dumps(self.payload(bill_date="2026-05-05")),
            content_type="application/json",
        )
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(Bill.objects.get(pk=pk).bill_date, date(2026, 3, 4))

    # ---- walk-ins ----

    def test_walk_in_creates_and_reuses_one_holding_account(self):
        r = self.save(self.walk_in())
        self.assertEqual(r.status_code, 200, r.content)
        bill = Bill.objects.get(pk=r.json()["bill_id"])
        self.assertTrue(bill.is_walk_in)
        self.assertEqual(bill.walk_in_name, "Sunil")
        self.assertTrue(bill.customer.is_walk_in_account)

        self.save(self.walk_in("Kamal"))
        self.assertEqual(Customer.objects.filter(is_walk_in_account=True).count(), 1)

    def test_walk_in_needs_a_name(self):
        r = self.save(self.walk_in("   "))
        self.assertEqual(r.status_code, 400)
        self.assertIn("name", r.json()["error"].lower())

    def test_walk_in_ignores_the_customer_dropdown(self):
        r = self.save(self.walk_in())
        self.assertNotEqual(
            Bill.objects.get(pk=r.json()["bill_id"]).customer_id, self.customer.pk
        )

    def test_walk_in_cannot_pay_later(self):
        """Debt on the shared holding account belongs to nobody."""
        r = self.save(self.payload(is_walk_in=True, walk_in_name="Sunil"))
        self.assertEqual(r.status_code, 400)
        self.assertIn("no account to put it on", r.json()["error"])

    def test_walk_in_leaves_the_holding_account_balance_flat(self):
        self.save(self.walk_in())
        account = Customer.objects.get(is_walk_in_account=True)
        self.assertEqual(account.balance, Decimal("0.00"))

    def test_walk_in_account_is_not_in_the_dropdown(self):
        self.save(self.walk_in())
        names = [c.name for c in self.client.get(reverse("core:bill_create")).context["customers"]]
        self.assertNotIn("Walk-in Customer", names)

    # ---- the page ----

    def test_create_page_renders_the_card_ui(self):
        html = self.client.get(reverse("core:bill_create")).content.decode()
        for marker in [
            'id="product-grid"', 'id="product-search"', 'id="category-tabs"',
            'id="bill-date"', 'id="walkin-name"', 'id="delivery-charge"',
            'id="discount-amount"', 'id="discount-reason"', 'id="grand-total"',
            'id="bill-rows"', 'id="step-build"', 'id="step-pay"',
        ]:
            self.assertIn(marker, html, marker)

    def test_category_tabs_come_from_the_database(self):
        """Operator-managed, so a hardcoded tab list would go stale."""
        html = self.client.get(reverse("core:bill_create")).content.decode()
        self.assertIn(f'data-category="{self.cat.pk}"', html)
        self.assertIn(f'data-category="{self.other.pk}"', html)
        self.assertIn('data-category=""', html)  # the All tab

    def test_no_leftover_checkbox_picker(self):
        """The old table UI is gone, not merely hidden."""
        html = self.client.get(reverse("core:bill_create")).content.decode()
        for gone in ['id="select-all"', 'id="add-selected"', 'id="add-modal"', 'id="product-rows"']:
            self.assertNotIn(gone, html, gone)

    def test_no_duplicate_ids_on_the_bill_pages(self):
        """getElementById returns the first, so a duplicate id shows up as a
        control that mysteriously does nothing."""
        pk = self.save(self.payload()).json()["bill_id"]
        for url in [reverse("core:bill_create"), reverse("core:bill_edit", args=[pk])]:
            with self.subTest(url=url):
                html = self.client.get(url).content.decode()
                ids = re.findall(r'\sid="([^"]+)"', html)
                dupes = [i for i, n in Counter(ids).items() if n > 1]
                self.assertEqual(dupes, [], f"duplicate ids: {dupes}")

    def test_walk_in_price_id_reaches_the_page(self):
        r = self.client.get(reverse("core:bill_create"))
        account = Customer.objects.get(is_walk_in_account=True)
        self.assertEqual(r.context["walk_in_customer_id"], account.pk)

    def test_bill_date_defaults_to_today_and_caps_at_today(self):
        html = self.client.get(reverse("core:bill_create")).content.decode()
        today = timezone.localdate().isoformat()
        self.assertIn(f'value="{today}"', html)
        self.assertIn(f'max="{today}"', html)

    def test_edit_page_hydrates_the_new_fields(self):
        pk = self.save(self.payload(
            bill_date="2026-03-04",
            delivery_charge="50.00",
            discount_amount="30.00",
            discount_reason="Damaged box",
        )).json()["bill_id"]
        initial = self.client.get(reverse("core:bill_edit", args=[pk])).context["initial"]
        self.assertEqual(initial["bill_date"], "2026-03-04")
        self.assertEqual(initial["delivery_charge"], "50.00")
        self.assertEqual(initial["discount_amount"], "30.00")
        self.assertEqual(initial["discount_reason"], "Damaged box")
