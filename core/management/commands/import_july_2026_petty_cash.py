"""One-shot importer: wipe ALL petty cash data and load the authoritative
July 2026 sheet. Destructive — take a backup first. Idempotent (re-running
wipes and reloads to the same state).

    python manage.py import_july_2026_petty_cash --yes

Totals it must reproduce (checked against the source sheet):
    expenses       = 496,436.00   (116 rows)
    reimbursements = 509,750.00   (7 rows, incl. 43,900 balance forward)
    closing        =  13,314.00
"""

from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from core.models import (
    PettyCashEntry,
    PettyCashFund,
    PettyCashReimbursement,
)

JULY = date(2026, 7, 1)

# (day, description, amount, receipt_no)
EXPENSES = [
    (1, "pathma motors bollero brake pad", "9360.00", ""),
    (1, "mosquito coil", "400.00", ""),
    (1, "FIRE extinguisher", "11200.00", ""),
    (2, "diesel", "9000.00", ""),
    (2, "Dilhara motors brake pad repair chargers", "2500.00", ""),
    (3, "june petty cash short", "14545.00", ""),
    (2, "Dilhara motors service", "28400.00", ""),
    (3, "20A power supply", "5600.00", ""),
    (3, "Colombo pick me hire(up,down,in)", "2700.00", ""),
    (3, "oil can & hummer", "3500.00", ""),
    (3, "terminal for oil pump", "700.00", ""),
    (4, "coloer print", "160.00", ""),
    (4, "prasanna stores 16x5 s-lon", "285.00", ""),
    (4, "rathnayaka lunch", "260.00", ""),
    (4, "super glue", "100.00", ""),
    (5, "for rathnayaka wheel hire & other expenses", "5000.00", ""),
    (6, "kanli chemicals", "8175.00", ""),
    (6, "highway chagers", "500.00", ""),
    (6, "deisal", "10000.00", ""),
    (6, "meal", "200.00", ""),
    (6, "Achchi donasion", "10100.00", ""),
    (7, "rathnayaka pickme hire", "1290.00", ""),
    (8, "Micro Bit Computers", "50850.00", ""),
    (8, "Rathnayaka wheel hire -UP", "1280.00", ""),
    (8, "highway chagers", "1500.00", ""),
    (8, "diesal", "6520.00", ""),
    (8, "Meals", "200.00", ""),
    (8, "photocopy", "210.00", ""),
    (8, "phone bill udara /sanjeewa", "2900.00", ""),
    (8, "selastic glue", "1980.00", ""),
    (8, "diesal", "10000.00", ""),
    (8, "highway chagers", "1000.00", ""),
    (8, "bolero light repir", "500.00", ""),
    (8, "sanwa electroinice", "1110.00", ""),
    (8, "colombo parking", "200.00", ""),
    (8, "Sumitra motos", "360.00", ""),
    (8, "martin electrical for SSR", "9000.00", ""),
    (9, "highway chagers", "400.00", ""),
    (9, "rathnayaka pick me", "1280.00", ""),
    (10, "rathnayaka pick me", "1350.00", ""),
    (10, "highway chagers", "500.00", ""),
    (10, "MEAL", "160.00", ""),
    (11, "DIESAL", "10200.00", ""),
    (11, "SOAP", "210.00", ""),
    (10, "FOR RATHNAYAKA MEAL", "1050.00", ""),
    (8, "FUEL COST", "3000.00", ""),
    (3, "RATHNAYAKA HIRE UP&DOWN", "2780.00", ""),
    (10, "RATHNAYAKA HIRE (DOWN)", "1500.00", ""),
    (10, "MEAL", "1000.00", ""),
    (9, "RATHNAYAKA HIRE DOWN", "1380.00", ""),
    (8, "RATHANAYAKA WHEEL HIRE", "1400.00", ""),
    (13, "NOZZLE & CITYZONE", "22000.00", ""),
    (16, "SLON GLUE", "200.00", ""),
    (16, "SML ENGINEERS ( PLATERS )", "22500.00", ""),
    (16, "DIESAL", "10500.00", ""),
    (16, "RAGAMA PHARMACY", "880.00", ""),
    (15, "BRIGHT ELECTRICALS", "2320.00", ""),
    (15, "highway chagers", "300.00", ""),
    (16, "MEAL", "200.00", ""),
    (17, "DIESEL", "10000.00", ""),
    (18, "SUMITHRA MOTORS", "300.00", ""),
    (18, "highway chagers", "500.00", ""),
    (20, "prasanna stores", "3910.00", ""),
    (20, "SUDATH MOTORS", "350.00", ""),
    (18, "MEAL HATTIPOLA/ THALATHUOYA", "800.00", ""),
    (18, "DIESEL", "10000.00", ""),
    (21, "RATHANAYAKA WHEEL HIRE UP", "1310.00", ""),
    (21, "EPF MAHIL -(MAY/JUNE)", "7000.00", ""),
    (20, "meal", "300.00", ""),
    (21, "elariees hardwear (bend)", "750.00", ""),
    (21, "prasnna hardwear 2m conduit", "780.00", "57847"),
    (21, "U.S.S Electrical ( cable 6mm)", "330.00", "43520"),
    (21, "elariees hardwear (bend)", "580.00", ""),
    (21, "meal", "160.00", "45"),
    (21, "highway chagers", "500.00", ""),
    (21, "diesal", "10000.00", ""),
    (21, 'prasanna hardwear (1"1/4x1 r/socket)', "1515.00", "56706"),
    (21, "U.S.S Electrical ( cable 16mm)", "900.00", "43525"),
    (21, "highway chagers", "500.00", ""),
    (21, "SUMITHRA MOTORS ( STS 10X1 1/4)", "60.00", "40477"),
    (21, "FOR RATHNAYAKA", "2230.00", ""),
    (22, "water bill july", "8000.00", ""),
    (22, "SLT BILL July", "2700.00", ""),
    (22, "dialog land line july", "1730.00", ""),
    (22, "Rathnayaka hire up", "1270.00", ""),
    (23, "advance for bill book", "3400.00", ""),
    (23, "dilhara motors clutch pump", "3500.00", "15689"),
    (23, "pathma motors- clutch slave", "18820.00", "4009495"),
    (23, "u.s.s electrical- togle switch", "120.00", "43578"),
    (23, "bread for tea time", "220.00", ""),
    (23, "antana house cowneter bar", "440.00", "66"),
    (23, "sanjeewa pickme", "220.00", ""),
    (24, "for T-80 machine hose fittings", "8260.00", "24217"),
    (24, "sanjeewa pickme", "300.00", ""),
    (24, "meal", "160.00", ""),
    (24, "diesal", "10000.00", ""),
    (24, "highway chagers", "600.00", ""),
    (24, "Ravago Lanka ( super white )", "1500.00", ""),
    (25, "prasanna stores (1/2 nipil for t-80 )", "4400.00", ""),
    (24, "20mm bul value", "2040.00", ""),
    (25, "t-80 matcion repir", "2680.00", ""),
    (27, "dinusha mobile bill", "1600.00", ""),
    (27, "pick me for surash nozzel riper ( dinuka )", "800.00", ""),
    (27, "meal for kithulgala & panadura", "540.00", ""),
    (27, "highway chagers", "500.00", ""),
    (28, "pick me for surash ( kaveesha )", "600.00", ""),
    (28, "bath dansala", "5000.00", ""),
    (27, "highway chagers", "550.00", ""),
    (27, "deisal", "10000.00", ""),
    (27, "MEAL", "200.00", ""),
    (30, "next month tea expences", "40000.00", ""),
    (30, "jayakodi super", "1116.00", ""),
    (31, "meal for sanjeewa", "200.00", ""),
    (31, "deisal", "10000.00", ""),
    (31, "highway chagers", "500.00", ""),
    (31, "bill book ( senovka )", "5000.00", ""),
]

# (day, amount, reason, given_by)
REIMBURSEMENTS = [
    (1, "43900.00", "Balance forward from June", "Balance forward"),
    (3, "80000.00", "Cash reimbursement", "Cash"),
    (7, "100000.00", "Cash reimbursement", "Cash"),
    (8, "50850.00", "Micro Bit Computers", "Micro Bit Computers"),
    (16, "100000.00", "Cash reimbursement", "Cash"),
    (22, "75000.00", "Cash reimbursement", "Cash"),
    (28, "60000.00", "Cash reimbursement", "Cash"),
]

EXPECTED_EXPENSES = Decimal("496436.00")
EXPECTED_REIMBURSEMENTS = Decimal("509750.00")
EXPECTED_CLOSING = Decimal("13314.00")


class Command(BaseCommand):
    help = "Wipe all petty cash data and import the July 2026 sheet."

    def add_arguments(self, parser):
        parser.add_argument(
            "--yes",
            action="store_true",
            help="Required. Confirms you have a backup and want to wipe & reload.",
        )

    def handle(self, *args, **options):
        if not options["yes"]:
            raise CommandError(
                "Refusing to run without --yes. This DELETES all petty cash "
                "data. Back up first (see below), then re-run with --yes:\n"
                "  python manage.py dumpdata core.PettyCashFund "
                "core.PettyCashEntry core.PettyCashReimbursement "
                "--indent 2 > petty_cash_backup.json"
            )

        User = get_user_model()
        user = User.objects.filter(is_superuser=True).order_by("id").first()
        if user is None:
            user = User.objects.order_by("id").first()
        if user is None:
            raise CommandError("No user found to own the imported rows.")

        # Pre-flight arithmetic: fail before touching data if the tables drift.
        exp_total = sum(Decimal(a) for _, _, a, _ in EXPENSES)
        reimb_total = sum(Decimal(a) for _, a, _, _ in REIMBURSEMENTS)
        if exp_total != EXPECTED_EXPENSES:
            raise CommandError(f"Expense total {exp_total} != {EXPECTED_EXPENSES}")
        if reimb_total != EXPECTED_REIMBURSEMENTS:
            raise CommandError(
                f"Reimbursement total {reimb_total} != {EXPECTED_REIMBURSEMENTS}"
            )

        with transaction.atomic():
            PettyCashEntry.objects.all().delete()
            PettyCashReimbursement.objects.all().delete()
            PettyCashFund.objects.all().delete()

            fund = PettyCashFund.objects.create(
                month=JULY,
                opening_balance=Decimal("0.00"),
                closing_balance=Decimal("0.00"),
            )

            PettyCashEntry.objects.bulk_create([
                PettyCashEntry(
                    fund=fund,
                    date=date(2026, 7, day),
                    description=desc,
                    category=PettyCashEntry.Category.OTHER,
                    amount=Decimal(amount),
                    entry_type=PettyCashEntry.EntryType.EXPENSE,
                    receipt_no=receipt,
                    added_by=user,
                )
                for day, desc, amount, receipt in EXPENSES
            ])

            PettyCashReimbursement.objects.bulk_create([
                PettyCashReimbursement(
                    fund=fund,
                    date=date(2026, 7, day),
                    amount=Decimal(amount),
                    reason=reason,
                    given_by=given_by,
                    added_by=user,
                )
                for day, amount, reason, given_by in REIMBURSEMENTS
            ])

            PettyCashFund.resync_chain()
            fund.refresh_from_db()

        self.stdout.write(f"Owner            : {user.get_username()}")
        self.stdout.write(f"Expenses loaded  : {len(EXPENSES)}  total {fund.total_expenses:,.2f}")
        self.stdout.write(f"Reimbursements   : {len(REIMBURSEMENTS)}  total {fund.total_reimbursements:,.2f}")
        self.stdout.write(f"Opening balance  : {fund.opening_balance:,.2f}")
        self.stdout.write(f"Closing / avail. : {fund.closing_balance:,.2f}")

        if fund.closing_balance != EXPECTED_CLOSING:
            raise CommandError(
                f"Closing {fund.closing_balance} != expected {EXPECTED_CLOSING} "
                "— rolled back."
            )
        self.stdout.write(self.style.SUCCESS("July 2026 petty cash imported and reconciled."))
