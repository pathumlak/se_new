"""Cheque maturity tracking: earlier-month cheques on the list, the Maturity
Soon desk, Maturity Check (bulk status) and returning to the same list spot
after an action."""

from datetime import timedelta
from decimal import Decimal
from urllib.parse import quote

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from .models import Bill, Cheque, Customer, Payment

User = get_user_model()


class ChequeMaturityBase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.today = timezone.localdate()
        cls.last_month = (cls.today.replace(day=1) - timedelta(days=5))
        cls.nimal = Customer.objects.create(name="Nimal")
        cls.kamal = Customer.objects.create(name="Kamal Traders")
        bill = Bill.objects.create(
            customer=cls.nimal,
            bill_date=cls.today,
            payment_type=Bill.PaymentType.FULL_CHEQUE,
        )
        cls.payment = Payment.objects.create(
            bill=bill,
            method=Payment.Method.CHEQUE,
            amount=Decimal("100.00"),
            paid_at=timezone.now(),
        )

    def setUp(self):
        self.client.force_login(
            User.objects.create_user(username="mgr", password="pw", role=User.Role.MANAGER)
        )

    def cheque(self, no, *, days, received=None, status=Cheque.Status.PENDING, customer=None):
        return Cheque.objects.create(
            payment=self.payment,
            customer=customer or self.nimal,
            cheque_no=no,
            bank_name="BOC",
            branch="Galle",
            amount=Decimal("100.00"),
            received_date=received or self.today,
            maturity_date=self.today + timedelta(days=days),
            status=status,
        )


class EarlierMonthOnTheListTests(ChequeMaturityBase):
    def numbers(self, **params):
        response = self.client.get(reverse("core:cheque_list"), params)
        self.response = response
        return {
            c.cheque_no: c
            for group in response.context["page_obj"].object_list
            for c in group["cheques"]
        }

    def test_pending_cheque_from_last_month_that_is_due_shows_up_tagged(self):
        self.cheque("OLD-DUE", days=1, received=self.last_month)
        rows = self.numbers()
        self.assertIn("OLD-DUE", rows)
        self.assertTrue(rows["OLD-DUE"].is_earlier_month)
        self.assertContains(self.response, "earlier month")

    def test_overdue_cheque_from_older_months_is_included(self):
        self.cheque("OLD-OVERDUE", days=-20, received=self.last_month)
        self.assertIn("OLD-OVERDUE", self.numbers())

    def test_last_month_cheques_that_are_not_actionable_stay_out(self):
        self.cheque("OLD-FAR", days=30, received=self.last_month)
        self.cheque("OLD-DEPOSITED", days=1, received=self.last_month, status=Cheque.Status.DEPOSITED)
        rows = self.numbers()
        self.assertNotIn("OLD-FAR", rows)
        self.assertNotIn("OLD-DEPOSITED", rows)

    def test_this_months_cheques_are_not_tagged(self):
        self.cheque("NEW", days=1)
        self.assertFalse(self.numbers()["NEW"].is_earlier_month)

    def test_a_past_month_view_is_not_padded_with_carry_over(self):
        self.cheque("OLD-DUE", days=1, received=self.last_month)
        rows = self.numbers(month=self.last_month.strftime("%Y-%m"))
        # Received in that month, so it belongs — but never tagged as earlier.
        self.assertIn("OLD-DUE", rows)
        self.assertFalse(rows["OLD-DUE"].is_earlier_month)

    def test_banner_counts_every_month(self):
        self.cheque("A", days=-1, received=self.last_month)
        self.cheque("B", days=0)
        self.numbers()
        summary = self.response.context["maturity_summary"]
        self.assertEqual(summary["total"], 2)
        self.assertEqual(summary["overdue"], 1)
        self.assertEqual(summary["due_today"], 1)
        self.assertEqual(summary["earlier"], 1)

    def test_maturity_labels(self):
        self.cheque("OVER", days=-3)
        self.cheque("TODAY", days=0)
        self.cheque("SOON", days=2)
        self.cheque("LATER", days=10)
        self.cheque("DONE", days=-3, status=Cheque.Status.DEPOSITED)
        rows = self.numbers()
        self.assertEqual(rows["OVER"].maturity_label, "Overdue 3 days")
        self.assertEqual(rows["TODAY"].maturity_label, "Due today")
        self.assertEqual(rows["SOON"].maturity_label, "Due in 2 days")
        self.assertEqual(rows["LATER"].maturity_label, "In 10 days")
        self.assertEqual(rows["DONE"].maturity_label, "")


class MaturitySoonListTests(ChequeMaturityBase):
    def rows(self, **params):
        response = self.client.get(reverse("core:cheque_maturity_soon"), params)
        self.response = response
        return [c.cheque_no for c in response.context["cheques"]]

    def setUp(self):
        super().setUp()
        self.cheque("OVERDUE-OLD", days=-30, received=self.last_month)
        self.cheque("TODAY", days=0)
        self.cheque("IN-5", days=5, customer=self.kamal)
        self.cheque("IN-12", days=12)
        self.cheque("IN-40", days=40)
        self.cheque("DEPOSITED", days=1, status=Cheque.Status.DEPOSITED)
        self.cheque("HELD", days=1, status=Cheque.Status.HELD)

    def test_default_is_pending_cheques_due_within_a_week_oldest_first(self):
        self.assertEqual(self.rows(), ["OVERDUE-OLD", "TODAY", "IN-5"])

    def test_includes_cheques_received_in_earlier_months(self):
        self.assertIn("OVERDUE-OLD", self.rows())

    def test_window_can_be_widened(self):
        self.assertEqual(self.rows(days=14), ["OVERDUE-OLD", "TODAY", "IN-5", "IN-12"])
        self.assertEqual(self.rows(days=3), ["OVERDUE-OLD", "TODAY"])

    def test_unknown_window_falls_back_to_default(self):
        self.assertEqual(self.rows(days="abc"), ["OVERDUE-OLD", "TODAY", "IN-5"])
        self.assertEqual(self.response.context["days"], 7)

    def test_all_pending_scope(self):
        self.assertEqual(
            self.rows(show="pending"),
            ["OVERDUE-OLD", "TODAY", "IN-5", "IN-12", "IN-40"],
        )

    def test_all_cheques_scope_includes_every_status_latest_maturity_first(self):
        listed = self.rows(show="all")
        self.assertEqual(len(listed), 7)
        self.assertEqual(listed[0], "IN-40")
        self.assertEqual(listed[-1], "OVERDUE-OLD")
        self.assertIn("DEPOSITED", listed)
        self.assertIn("HELD", listed)

    def test_all_scope_can_be_narrowed_by_status(self):
        self.assertEqual(self.rows(show="all", status="held"), ["HELD"])

    def test_customer_filter(self):
        self.assertEqual(self.rows(customer=self.kamal.pk), ["IN-5"])

    def test_tab_counts_describe_each_scope(self):
        self.rows()
        self.assertEqual(
            self.response.context["scope_counts"], {"soon": 3, "pending": 5, "all": 7}
        )

    def test_unknown_scope_falls_back_to_soon(self):
        self.assertEqual(self.rows(show="zzz"), ["OVERDUE-OLD", "TODAY", "IN-5"])

    def test_check_mode_adds_checkboxes_and_the_bulk_bar(self):
        html = self.client.get(reverse("core:cheque_maturity_soon"), {"check": "1"}).content.decode()
        self.assertIn('name="cheque_ids"', html)
        self.assertIn('id="bulk-form"', html)
        self.assertIn(reverse("core:cheque_bulk_status"), html)

    def test_normal_mode_has_no_checkboxes(self):
        html = self.client.get(reverse("core:cheque_maturity_soon")).content.decode()
        self.assertNotIn('name="cheque_ids"', html)

    def test_requires_login(self):
        self.client.logout()
        response = self.client.get(reverse("core:cheque_maturity_soon"))
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("login"), response["Location"])

    def test_the_main_list_requires_login_too(self):
        self.client.logout()
        response = self.client.get(reverse("core:cheque_list"))
        self.assertEqual(response.status_code, 302)


class KeepPositionTests(ChequeMaturityBase):
    def test_action_returns_to_the_page_and_filters_it_came_from(self):
        cheque = self.cheque("C1", days=0)
        back = "/cheques/?month=all&status=pending&sort=customer&page=3"
        response = self.client.post(
            reverse("core:cheque_deposit", args=[cheque.pk]), {"next": back}
        )
        self.assertRedirects(response, back, fetch_redirect_response=False)
        cheque.refresh_from_db()
        self.assertEqual(cheque.status, Cheque.Status.DEPOSITED)

    def test_every_action_honours_next(self):
        back = "/cheques/maturity-soon/?show=pending&page=2"
        for name, extra in (
            ("core:cheque_hold", {}),
            ("core:cheque_bounce", {"bounce_new_date": "2030-01-01"}),
        ):
            cheque = self.cheque(f"X-{name}", days=0)
            response = self.client.post(reverse(name, args=[cheque.pk]), {"next": back, **extra})
            self.assertRedirects(response, back, fetch_redirect_response=False)

    def test_a_failed_bounce_also_returns_to_the_same_page(self):
        cheque = self.cheque("C1", days=0)
        back = "/cheques/?month=all&page=2"
        response = self.client.post(
            reverse("core:cheque_bounce", args=[cheque.pk]), {"next": back}
        )
        self.assertRedirects(response, back, fetch_redirect_response=False)

    def test_without_next_it_falls_back_to_the_list(self):
        cheque = self.cheque("C1", days=0)
        response = self.client.post(reverse("core:cheque_deposit", args=[cheque.pk]))
        self.assertRedirects(response, reverse("core:cheque_list"))

    def test_next_pointing_off_site_is_ignored(self):
        cheque = self.cheque("C1", days=0)
        for evil in ("https://evil.example/steal", "//evil.example/x", "javascript:alert(1)"):
            cheque.refresh_from_db()
            Cheque.objects.filter(pk=cheque.pk).update(status=Cheque.Status.PENDING)
            response = self.client.post(
                reverse("core:cheque_deposit", args=[cheque.pk]), {"next": evil}
            )
            self.assertRedirects(response, reverse("core:cheque_list"))

    def test_list_forms_carry_the_current_page_as_next(self):
        self.cheque("C1", days=0)
        html = self.client.get(reverse("core:cheque_list"), {"month": "all"}).content.decode()
        self.assertIn('name="next"', html)
        self.assertIn("data-keep-position", html)
        self.assertIn("/cheques/?month=all", html)

    def test_edit_link_carries_next_and_edit_returns_there(self):
        cheque = self.cheque("C1", days=0)
        back = "/cheques/?month=all&page=2"

        html = self.client.get(reverse("core:cheque_list"), {"month": "all"}).content.decode()
        self.assertIn(f"?next={quote('/cheques/?month=all')}", html)

        page = self.client.get(reverse("core:cheque_edit", args=[cheque.pk]), {"next": back})
        self.assertContains(page, 'name="next"')
        self.assertContains(page, "page=2")

        response = self.client.post(
            reverse("core:cheque_edit", args=[cheque.pk]),
            {
                "next": back,
                "cheque_no": "C1", "bank_name": "BOC", "branch": "", "acc_no": "",
                "amount": "100.00",
                "received_date": cheque.received_date.isoformat(),
                "received_date_change_reason": "",
                "maturity_date": (self.today + timedelta(days=9)).isoformat(),
                "status": "pending", "bounce_new_date": "",
            },
        )
        self.assertRedirects(response, back, fetch_redirect_response=False)
        cheque.refresh_from_db()
        self.assertEqual(cheque.maturity_date, self.today + timedelta(days=9))


class MaturityCheckBulkTests(ChequeMaturityBase):
    def setUp(self):
        super().setUp()
        self.a = self.cheque("A", days=0)
        self.b = self.cheque("B", days=1, customer=self.kamal)
        self.url = reverse("core:cheque_bulk_status")
        self.back = "/cheques/maturity-soon/?check=1&page=2"

    def post(self, ids, action, **extra):
        return self.client.post(
            self.url,
            {"cheque_ids": ids, "action": action, "next": self.back, **extra},
        )

    def test_bulk_deposit(self):
        response = self.post([self.a.pk, self.b.pk], "deposit")
        self.assertRedirects(response, self.back, fetch_redirect_response=False)
        statuses = set(Cheque.objects.values_list("status", flat=True))
        self.assertEqual(statuses, {Cheque.Status.DEPOSITED})

    def test_bulk_hold_moves_the_balance_like_a_single_hold(self):
        self.nimal.refresh_from_db()
        before = self.nimal.balance
        self.post([self.a.pk], "hold")
        self.a.refresh_from_db()
        self.nimal.refresh_from_db()
        self.assertEqual(self.a.status, Cheque.Status.HELD)
        # A pending cheque is credited; holding it takes the credit back off.
        self.assertEqual(self.nimal.balance, before - Decimal("100.00"))

    def test_bulk_return_needs_a_date(self):
        response = self.post([self.a.pk], "bounce")
        self.assertRedirects(response, self.back, fetch_redirect_response=False)
        self.a.refresh_from_db()
        self.assertEqual(self.a.status, Cheque.Status.PENDING)

        self.post([self.a.pk], "bounce", bounce_new_date="2030-02-01")
        self.a.refresh_from_db()
        self.assertEqual(self.a.status, Cheque.Status.BOUNCED)
        self.assertEqual(str(self.a.bounce_new_date), "2030-02-01")

    def test_nothing_ticked_changes_nothing(self):
        response = self.post([], "deposit", follow=False)
        self.assertRedirects(response, self.back, fetch_redirect_response=False)
        self.assertFalse(Cheque.objects.filter(status=Cheque.Status.DEPOSITED).exists())

    def test_unknown_action_changes_nothing(self):
        self.post([self.a.pk], "explode")
        self.a.refresh_from_db()
        self.assertEqual(self.a.status, Cheque.Status.PENDING)

    def test_cheques_already_in_the_status_are_skipped_not_double_counted(self):
        self.post([self.a.pk], "hold")
        self.nimal.refresh_from_db()
        balance = self.nimal.balance
        self.post([self.a.pk, self.b.pk], "hold")
        self.nimal.refresh_from_db()
        self.assertEqual(self.nimal.balance, balance)  # A wasn't held twice
        self.b.refresh_from_db()
        self.assertEqual(self.b.status, Cheque.Status.HELD)

    def test_bulk_requires_post_and_login(self):
        self.assertEqual(self.client.get(self.url).status_code, 405)
        self.client.logout()
        self.assertEqual(self.post([self.a.pk], "deposit").status_code, 302)
        self.a.refresh_from_db()
        self.assertEqual(self.a.status, Cheque.Status.PENDING)
