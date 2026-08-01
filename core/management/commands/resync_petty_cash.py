from django.core.management.base import BaseCommand

from core.models import PettyCashFund


class Command(BaseCommand):
    help = (
        "Re-sync every petty cash month so each month's opening balance equals "
        "the previous month's closing. Fixes opening balances that were frozen "
        "as stale snapshots before the running-balance change. Idempotent."
    )

    def handle(self, *args, **options):
        before = [
            (f.month, f.opening_balance, f.closing_balance)
            for f in PettyCashFund.objects.order_by("month")
        ]

        PettyCashFund.resync_chain()

        after = {
            f.month: (f.opening_balance, f.closing_balance)
            for f in PettyCashFund.objects.order_by("month")
        }

        if not before:
            self.stdout.write("No petty cash funds found — nothing to re-sync.")
            return

        self.stdout.write("Month        Opening (old -> new)   Closing (old -> new)")
        for month, old_open, old_close in before:
            new_open, new_close = after[month]
            changed = "  <-- changed" if (
                new_open != old_open or new_close != old_close
            ) else ""
            self.stdout.write(
                f"{month:%Y-%m}   "
                f"{old_open:>12,.2f} -> {new_open:>12,.2f}   "
                f"{old_close:>12,.2f} -> {new_close:>12,.2f}{changed}"
            )

        self.stdout.write(self.style.SUCCESS("Petty cash re-synced."))
