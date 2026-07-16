"""Report cheques that are maturing and still haven't been banked.

Built for cron, so the exit code carries the answer: 0 when there is nothing to
chase, 1 when there is. That lets a wrapper act on it without parsing stdout.

    0 8 * * * /path/to/venv/bin/python /path/to/manage.py notify_cheques >> /var/log/senovka_cheques.log 2>&1
"""

import sys
from datetime import timedelta

from django.conf import settings
from django.core.mail import mail_admins
from django.core.management.base import BaseCommand
from django.utils import timezone

from core.models import Cheque
from core.views import CHEQUE_WARNING_DAYS

#: Column widths for the stdout table.
COLUMNS = [
    ("Customer", 24),
    ("Cheque No", 14),
    ("Bank", 16),
    ("Amount", 14),
    ("Maturity Date", 15),
    ("Days Left", 10),
]


def maturing_cheques(horizon):
    """The same question the dashboard asks: pending, and due by the horizon.

    No lower bound — a cheque that matured last week and still hasn't been
    banked is the one most worth shouting about.
    """
    return (
        Cheque.objects.filter(maturity_date__lte=horizon, status=Cheque.Status.PENDING)
        .select_related("customer")
        .order_by("maturity_date", "cheque_no")
    )


def days_left_text(cheque, today):
    days = (cheque.maturity_date - today).days
    if days < 0:
        return f"{-days} overdue"
    if days == 0:
        return "today"
    return str(days)


def render_table(cheques, today):
    header = "  ".join(name.ljust(width) for name, width in COLUMNS)
    rule = "  ".join("-" * width for _, width in COLUMNS)

    lines = [header, rule]
    for cheque in cheques:
        lines.append(
            "  ".join(
                [
                    cheque.customer.name[:24].ljust(24),
                    cheque.cheque_no[:14].ljust(14),
                    cheque.bank_name[:16].ljust(16),
                    f"{cheque.amount:,.2f}".rjust(14),
                    cheque.maturity_date.strftime("%Y-%m-%d").ljust(15),
                    days_left_text(cheque, today).rjust(10),
                ]
            )
        )
    return "\n".join(lines)


class Command(BaseCommand):
    help = "Report pending cheques maturing within %s days." % CHEQUE_WARNING_DAYS

    def add_arguments(self, parser):
        parser.add_argument(
            "--days",
            type=int,
            default=CHEQUE_WARNING_DAYS,
            help=f"How many days ahead to look (default {CHEQUE_WARNING_DAYS}).",
        )
        parser.add_argument(
            "--no-email",
            action="store_true",
            help="Print the table only, even when ADMINS is configured.",
        )

    def handle(self, *args, **options):
        today = timezone.localdate()
        horizon = today + timedelta(days=options["days"])
        cheques = list(maturing_cheques(horizon))

        if not cheques:
            self.stdout.write(
                f"No pending cheques maturing on or before {horizon:%Y-%m-%d}."
            )
            sys.exit(0)

        total = sum(cheque.amount for cheque in cheques)
        table = render_table(cheques, today)
        summary = (
            f"{len(cheques)} pending cheque{'' if len(cheques) == 1 else 's'} "
            f"maturing on or before {horizon:%Y-%m-%d}, "
            f"totalling {total:,.2f}."
        )

        self.stdout.write(summary)
        self.stdout.write("")
        self.stdout.write(table)

        if not options["no_email"]:
            self.email(summary, table)

        # Cheques found. Non-zero so cron, or whatever wraps it, can tell.
        sys.exit(1)

    def email(self, summary, table):
        """Mail the admins, when there are any to mail.

        mail_admins is a no-op without ADMINS, but say so rather than leave the
        operator wondering whether mail went out.
        """
        if not getattr(settings, "ADMINS", None):
            self.stdout.write("")
            self.stdout.write("ADMINS is not configured, so no email was sent.")
            return

        if not getattr(settings, "EMAIL_BACKEND", None):
            self.stdout.write("")
            self.stdout.write("EMAIL_BACKEND is not configured, so no email was sent.")
            return

        mail_admins(
            subject=f"Senovka: {summary}",
            message=f"{summary}\n\n{table}\n",
            fail_silently=False,
        )
        recipients = ", ".join(email for _, email in settings.ADMINS)
        self.stdout.write("")
        self.stdout.write(f"Emailed {recipients}.")
