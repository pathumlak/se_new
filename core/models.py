import secrets
from decimal import Decimal

from django.contrib.auth.models import AbstractUser
from django.db import models, transaction
from django.db.models import F

#: Deliberately missing I, l, 1, O and 0. A generated password is read off a
#: screen and typed in by hand by someone who has never seen it before, so a
#: character that can be mistaken for another one is a support call.
PASSWORD_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789"
PASSWORD_LENGTH = 8


def generate_password():
    """A fresh password for a new or reset account.

    `secrets`, not `random`: this is a credential, and random's Mersenne
    Twister is predictable from enough prior output.
    """
    return "".join(secrets.choice(PASSWORD_ALPHABET) for _ in range(PASSWORD_LENGTH))


class User(AbstractUser):
    """Application user. Accounts are seeded, never self-registered."""

    class Role(models.TextChoices):
        SUPER_ADMIN = "super_admin", "Super Admin"
        MANAGER = "manager", "Manager"

    role = models.CharField(max_length=20, choices=Role.choices, default=Role.MANAGER)

    def __str__(self):
        return f"{self.username} ({self.get_role_display()})"

    @property
    def full_name(self):
        """For the user list, which has a column to fill even when the name
        fields are blank."""
        return self.get_full_name() or ""


class Category(models.Model):
    name = models.CharField(max_length=100, unique=True)
    description = models.TextField(blank=True)

    class Meta:
        verbose_name_plural = "categories"
        ordering = ["name"]

    def __str__(self):
        return self.name


class Product(models.Model):
    name = models.CharField(max_length=150)
    size = models.CharField(max_length=50, blank=True)
    qty = models.DecimalField(max_digits=12, decimal_places=3, default=0)
    category = models.ForeignKey(
        Category,
        on_delete=models.PROTECT,
        related_name="products",
    )
    default_price = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name", "size"]

    def __str__(self):
        return f"{self.name} {self.size}".strip()


class Customer(models.Model):
    """A trading party. Suppliers are customers with is_supplier=True."""

    name = models.CharField(max_length=150)
    phone = models.CharField(max_length=30, blank=True)
    email = models.EmailField(max_length=254, blank=True, default="", null=True)
    address = models.TextField(blank=True)
    credit_limit = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    balance = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    is_supplier = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)

    # The single holding account every walk-in bill hangs off. Bill.customer is
    # required, so a sale to someone with no account still needs a row to point
    # at; who actually bought the goods is Bill.walk_in_name.
    #
    # A flag rather than a name lookup: the name is free text the operator can
    # edit, and matching on it would mint a second holding account the first
    # time someone renamed this one.
    is_walk_in_account = models.BooleanField(default=False)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name



class CustomerBalanceAdjustment(models.Model):
    """A manual correction to a customer's running balance.

    Not a Bill, not a Payment, not a BillSettlement: those all move money
    against a specific bill. This one moves the customer's balance without any
    invoice or receipt behind it — a starting balance being seeded, an old
    write-off being reversed, an off-book credit being granted. The rule the
    ledger relies on is that every source of movement has to be tied back to a
    row somewhere, so it exists rather than the alternative of editing
    Customer.balance in place with no history.

    Sign convention here matches Customer.balance itself, not the ledger view:
      credit  — Customer.balance += amount   (we owe them, or they owe us less)
      debit   — Customer.balance -= amount   (they owe us more)

    amount is always positive; the direction is on `adjustment_type`. Storing
    a signed amount would let a "-500 credit" mean the same thing as a "500
    debit" and the reports would have to canonicalise every row.
    """

    class Type(models.TextChoices):
        CREDIT = "credit", "Credit (+)"
        DEBIT = "debit", "Debit (-)"

    customer = models.ForeignKey(
        Customer,
        on_delete=models.CASCADE,
        related_name="balance_adjustments",
    )
    adjustment_type = models.CharField(max_length=10, choices=Type.choices)
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    reason = models.CharField(max_length=500)
    adjustment_date = models.DateField()
    adjusted_by = models.ForeignKey(
        User,
        on_delete=models.PROTECT,
        related_name="balance_adjustments",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-adjustment_date", "-id"]

    def __str__(self):
        sign = "+" if self.adjustment_type == self.Type.CREDIT else "-"
        return f"Adjustment {sign}{self.amount} · {self.customer}"

    @property
    def signed_amount(self):
        """The amount as it moves Customer.balance: positive for credit,
        negative for debit."""
        if self.adjustment_type == self.Type.CREDIT:
            return self.amount
        return -self.amount


class CustomerPrice(models.Model):
    """Per-customer negotiated price, overriding Product.default_price."""

    customer = models.ForeignKey(
        Customer,
        on_delete=models.CASCADE,
        related_name="custom_prices",
    )
    product = models.ForeignKey(
        Product,
        on_delete=models.CASCADE,
        related_name="customer_prices",
    )
    unit_price = models.DecimalField(max_digits=12, decimal_places=2)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ("customer", "product")
        ordering = ["customer", "product"]

    def __str__(self):
        return f"{self.customer} · {self.product} @ {self.unit_price}"


class Bill(models.Model):
    class PaymentType(models.TextChoices):
        FULL_CASH = "full_cash", "Full Cash"
        FULL_CHEQUE = "full_cheque", "Full Cheque"
        # PARTIAL is the pre-split "cash + cheque, full amount" type. Kept
        # only for legacy rows; new bills use PARTIAL_CASH or PARTIAL_CHEQUE.
        PARTIAL = "partial", "Partial (legacy)"
        PARTIAL_CASH = "partial_cash", "Partial Cash"
        PARTIAL_CHEQUE = "partial_cheque", "Partial Cheque"
        MIXED = "mixed", "Mixed"
        PAY_LATER = "pay_later", "Pay Later"

    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        UNPAID = "unpaid", "Unpaid"
        PARTIAL = "partial", "Partially Paid"
        PAID = "paid", "Paid"
        CANCELLED = "cancelled", "Cancelled"

    # Null for a walk-in sale: there is no account to point at, and
    # walk_in_name is the only record of who bought the goods. Every other
    # bill keeps this required — the ledger has to balance somewhere.
    customer = models.ForeignKey(
        Customer,
        on_delete=models.PROTECT,
        related_name="bills",
        null=True,
        blank=True,
    )
    bill_date = models.DateField()
    subtotal = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    delivery_charge = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    discount_amount = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    discount_reason = models.CharField(max_length=255, blank=True)
    total_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    paid_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    # Written off rather than collected — see BillSettlement, which is the only
    # thing that should ever move this.
    settled_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    # How much of the customer's positive balance (credit we owed them) this
    # bill consumed. Stored as a snapshot at save time so the bill detail can
    # show it as a line item — the balance itself has already been moved by
    # `balance_change`, so this is display-only and never re-applied.
    credit_applied = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    # Signed: how this bill moved the customer's running balance.
    balance_change = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    payment_type = models.CharField(max_length=20, choices=PaymentType.choices)
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.UNPAID
    )
    notes = models.TextField(blank=True)

    # A sale to someone with no account. The customer FK still points at a
    # walk-in holding account, so the ledger stays whole; walk_in_name is who
    # actually took the goods.
    is_walk_in = models.BooleanField(default=False)
    walk_in_name = models.CharField(max_length=255, blank=True)

    edit_reason = models.CharField(max_length=500, blank=True)
    edit_date = models.DateField(null=True, blank=True)

    class Meta:
        ordering = ["-bill_date", "-id"]

    def __str__(self):
        who = self.customer if self.customer_id else f"{self.walk_in_name} (walk-in)"
        return f"Bill #{self.pk} · {who} · {self.total_amount}"

    @property
    def remaining_balance(self):
        """What is still owed on this bill: neither collected nor written off."""
        return self.total_amount - self.paid_amount - self.settled_amount

    @property
    def amount_to_collect(self):
        """What the customer had to hand over at the till: bill total minus any
        credit already sitting on their account that this bill consumed.

        Display-only. The actual money moves are still `paid_amount` (what
        came in) and `balance_change` (how the account moved). This exists so
        the bill detail can show a line the customer would recognise from
        their own paperwork.
        """
        return self.total_amount - self.credit_applied


class BillItem(models.Model):
    bill = models.ForeignKey(
        Bill,
        on_delete=models.CASCADE,
        related_name="items",
    )
    product = models.ForeignKey(
        Product,
        on_delete=models.PROTECT,
        related_name="bill_items",
    )
    qty = models.DecimalField(max_digits=12, decimal_places=3)
    unit_price = models.DecimalField(max_digits=12, decimal_places=2)
    line_total = models.DecimalField(max_digits=12, decimal_places=2)

    def __str__(self):
        return f"{self.product} × {self.qty} = {self.line_total}"


class BillSettlement(models.Model):
    """Money agreed off a bill after the fact, rather than collected on it.

    Kept apart from Payment because the two answer different questions: a
    payment is cash that arrived, a settlement is debt that stopped being owed.
    Netting them into paid_amount would make a bill that was written down look
    like a bill that was paid.
    """

    class Method(models.TextChoices):
        CASH = "cash", "Cash"
        CHEQUE = "cheque", "Cheque"

    bill = models.ForeignKey(
        Bill,
        on_delete=models.CASCADE,
        related_name="settlements",
    )
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    method = models.CharField(max_length=20, choices=Method.choices)
    settlement_date = models.DateField()
    reason = models.CharField(max_length=500)
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey(
        User,
        on_delete=models.PROTECT,
        related_name="settlements",
    )

    class Meta:
        ordering = ["-settlement_date", "-id"]

    def __str__(self):
        return f"Settlement {self.amount} · Bill #{self.bill_id}"

    def save(self, *args, **kwargs):
        """Post the settlement to the bill and the customer, once.

        Only a brand-new row posts. Re-saving an existing settlement leaves the
        figures alone, because there is no record of what the old amount was to
        take back off first — editing one means reversing it and writing a new
        one, the same shape as bill edits in views.py.
        """
        is_new = self._state.adding

        with transaction.atomic():
            super().save(*args, **kwargs)
            if not is_new:
                return

            Bill.objects.filter(pk=self.bill_id).update(
                settled_amount=F("settled_amount") + self.amount,
                # Keeps new_balance = old_balance + balance_change true, which
                # is what _reverse_bill relies on to undo an edited or deleted
                # bill. Without this the settlement would survive the reversal
                # and strand the customer's balance.
                balance_change=F("balance_change") + self.amount,
            )
            # Debtors run negative, so forgiving debt moves the balance up.
            customer_id = Bill.objects.values_list("customer_id", flat=True).get(
                pk=self.bill_id
            )
            Customer.objects.filter(pk=customer_id).update(
                balance=F("balance") + self.amount
            )


class BillEditAudit(models.Model):
    """A note that a bill was rewritten, and why.

    Deliberately not a BillSettlement: a settlement moves money, and its save()
    posts an amount to the bill and the customer. An edit note moves nothing —
    the edit itself already reversed and re-applied every figure. This carries
    no amount at all, so it can never touch a balance.

    One row per edit rather than one per bill: Bill.edit_date/edit_reason hold
    only the most recent edit, and the ledger has to show each one on the day it
    happened. Nothing here cascades a balance, so a row surviving its own
    correction is only ever a note.
    """

    bill = models.ForeignKey(
        Bill,
        on_delete=models.CASCADE,
        related_name="edit_audits",
    )
    edit_date = models.DateField()
    reason = models.CharField(max_length=500)
    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey(
        User,
        on_delete=models.PROTECT,
        related_name="bill_edit_audits",
    )

    class Meta:
        ordering = ["-edit_date", "-id"]

    def __str__(self):
        return f"Bill #{self.bill_id} edited {self.edit_date} · {self.reason}"


class Payment(models.Model):
    class Method(models.TextChoices):
        CASH = "cash", "Cash"
        CHEQUE = "cheque", "Cheque"
        TRANSFER = "transfer", "Transfer"

    class Account(models.TextChoices):
        SENOVKA = "senovka", "Senovka"
        DINUSHA = "dinusha", "Dinusha"

    # Nullable so a settlement can arrive without a bill behind it — a
    # payment against an opening balance, or a top-up that sits as customer
    # credit for the next bill. When `bill` is set the payment cascades on
    # bill delete as before; when it isn't, it hangs off `customer` instead
    # so the ledger and the cheque list can still find it.
    bill = models.ForeignKey(
        Bill,
        on_delete=models.CASCADE,
        related_name="payments",
        null=True,
        blank=True,
    )
    customer = models.ForeignKey(
        Customer,
        on_delete=models.PROTECT,
        related_name="direct_payments",
        null=True,
        blank=True,
    )
    method = models.CharField(max_length=20, choices=Method.choices)
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    account = models.CharField(max_length=20, choices=Account.choices, blank=True)
    paid_at = models.DateTimeField()

    class Meta:
        ordering = ["-paid_at", "-id"]

    def __str__(self):
        who = f"Bill #{self.bill_id}" if self.bill_id else (
            f"customer #{self.customer_id}" if self.customer_id else "detached"
        )
        return f"{self.get_method_display()} {self.amount} · {who}"


class Cheque(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        DEPOSITED = "deposited", "Deposited"
        BOUNCED = "bounced", "Bounced"
        HELD = "held", "Held"

    payment = models.ForeignKey(
        Payment,
        on_delete=models.CASCADE,
        related_name="cheques",
    )
    # Set when the cheque arrived at settlement time rather than on the bill
    # itself, which is also what lets one bill carry several cheques.
    bill = models.ForeignKey(
        Bill,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="cheques",
    )
    customer = models.ForeignKey(
        Customer,
        on_delete=models.PROTECT,
        related_name="cheques",
    )
    cheque_no = models.CharField(max_length=50)
    bank_name = models.CharField(max_length=100)
    branch = models.CharField(max_length=100, blank=True)
    acc_no = models.CharField(max_length=50, blank=True)
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    received_date = models.DateField()
    maturity_date = models.DateField()
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.PENDING
    )
    # Re-presentation date agreed with the customer after a bounce.
    bounce_new_date = models.DateField(null=True, blank=True)

    class Meta:
        ordering = ["maturity_date", "-id"]

    def __str__(self):
        return f"Cheque {self.cheque_no} · {self.bank_name} · {self.amount}"


class CashTransfer(models.Model):
    class Account(models.TextChoices):
        SENOVKA = "senovka", "Senovka"
        DINUSHA = "dinusha", "Dinusha"

    payment = models.ForeignKey(
        Payment,
        on_delete=models.CASCADE,
        related_name="transfers",
    )
    to_account = models.CharField(max_length=20, choices=Account.choices)
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    transferred_at = models.DateTimeField()

    class Meta:
        ordering = ["-transferred_at", "-id"]

    def __str__(self):
        return f"{self.amount} to {self.get_to_account_display()}"


class CashDrawer(models.Model):
    class TxnType(models.TextChoices):
        IN = "in", "In"
        OUT = "out", "Out"
        TRANSFER = "transfer", "Transfer"

    txn_date = models.DateField()
    txn_type = models.CharField(max_length=20, choices=TxnType.choices)
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    reason = models.CharField(max_length=255, blank=True)
    bill = models.ForeignKey(
        Bill,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="cash_drawer_entries",
    )

    edit_reason = models.CharField(max_length=500, blank=True)
    edited_at = models.DateTimeField(null=True, blank=True)
    edited_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="edited_drawer_entries",
    )

    class Meta:
        ordering = ["-txn_date", "-id"]

    def __str__(self):
        return f"{self.get_txn_type_display()} {self.amount} on {self.txn_date}"


class SupplierBill(models.Model):
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        UNPAID = "unpaid", "Unpaid"
        PARTIAL = "partial", "Partially Paid"
        PAID = "paid", "Paid"
        CANCELLED = "cancelled", "Cancelled"

    supplier = models.ForeignKey(
        Customer,
        on_delete=models.PROTECT,
        related_name="supplier_bills",
    )
    bill_date = models.DateField()
    total_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    paid_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.UNPAID
    )
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["-bill_date", "-id"]

    def __str__(self):
        return f"Supplier Bill #{self.pk} · {self.supplier} · {self.total_amount}"


class SupplierBillItem(models.Model):
    supplier_bill = models.ForeignKey(
        SupplierBill,
        on_delete=models.CASCADE,
        related_name="items",
    )
    product = models.ForeignKey(
        Product,
        on_delete=models.PROTECT,
        related_name="supplier_bill_items",
    )
    qty = models.DecimalField(max_digits=12, decimal_places=3)
    unit_price = models.DecimalField(max_digits=12, decimal_places=2)
    line_total = models.DecimalField(max_digits=12, decimal_places=2)

    def __str__(self):
        return f"{self.product} × {self.qty} = {self.line_total}"


class HeldBill(models.Model):
    """A bill parked mid-entry, to be recalled and finished later.

    A held bill is dormant — no stock moves, no balance changes, no payment
    rows are written. It holds the raw form payload as JSON, exactly as the
    bill creation page would post it, so recalling it hydrates the page and
    saving it goes through the normal _write_bill path. When saved as a real
    bill the held record is deleted.

    Nothing here uses Bill because a held bill has nothing to count yet:
    borrowing that model would mean the biller could see draft bills in the
    bill list, in ledgers, and against the stock the parked lines haven't
    actually taken.
    """

    # For at-a-glance context in the list. Nullable so a walk-in draft can be
    # held without picking anyone; the name below is the fallback.
    customer = models.ForeignKey(
        Customer,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="held_bills",
    )
    walk_in_name = models.CharField(max_length=255, blank=True)

    # The whole form payload the biller would have posted, verbatim. Read
    # back by the recall page which fires the same code path bill edit uses.
    payload = models.JSONField()

    # Cached from the payload so the list can order and search without
    # unpicking it.
    label = models.CharField(max_length=255, blank=True)
    item_count = models.PositiveIntegerField(default=0)
    subtotal = models.DecimalField(max_digits=12, decimal_places=2, default=0)

    created_by = models.ForeignKey(
        User,
        on_delete=models.PROTECT,
        related_name="held_bills",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-updated_at", "-id"]

    def __str__(self):
        return self.label or f"Held bill #{self.pk}"


class PettyCashFund(models.Model):
    """One month's petty cash float.

    Exactly one row per month, which is what `month` being unique enforces. The
    month is stored as its first day rather than a year/month pair so it can be
    compared, ordered and filtered as a date like every other date in the
    system; `PettyCashFund.for_month` is the only thing that should build one,
    and it normalises to day 1.

    opening_balance is a snapshot, not a lookup: it is copied from the previous
    month's closing balance when the fund is created and then left alone. Were
    it derived on read, correcting a six-month-old expense would silently
    rewrite every opening balance since, and the tin on the shelf would no
    longer match any of them.
    """

    month = models.DateField(unique=True)
    opening_balance = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    # Cached from the entries below, recomputed by `recalculate` after anything
    # that moves them. Stored rather than derived so the list, the carry-forward
    # and the PDF all read one number instead of three aggregate queries that
    # could disagree.
    closing_balance = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-month"]

    def __str__(self):
        return f"Petty cash · {self.month:%B %Y}"

    @classmethod
    def for_month(cls, month):
        """The fund for `month`, created with a carried-forward opening balance
        if it does not exist yet.

        Returns (fund, carried_from) where carried_from is the fund the opening
        balance came from, or None when nothing was carried — either because the
        fund already existed, or because this is the first month there has ever
        been. The caller uses it to decide whether to show the carry-forward
        notice; it is not stored, because it is a fact about this request, not
        about the fund.

        get_or_create rather than exists()/create(): two people opening the page
        on the 1st of the month would otherwise both find nothing and both
        create a fund, and `month` is unique, so the loser would 500.
        """
        first = month.replace(day=1)

        previous = (
            cls.objects.filter(month__lt=first).order_by("-month").first()
        )
        opening = previous.closing_balance if previous else Decimal("0.00")

        fund, created = cls.objects.get_or_create(
            month=first,
            defaults={"opening_balance": opening, "closing_balance": opening},
        )
        return fund, (previous if created and previous else None)

    def recalculate(self):
        """Rewrite closing_balance from the entries that make it up.

        Called after every write to an entry or a reimbursement rather than
        adjusting by a delta, because a full recount cannot drift: an edit that
        reverses 500 and applies 600 has no way to leave the fund 100 out if the
        fund is never told about either number.

        Only this month's closing balance moves. A later month's opening balance
        was snapshotted when it was created and is deliberately left alone — see
        the class docstring.
        """
        expenses = self.entries.filter(
            entry_type=PettyCashEntry.EntryType.EXPENSE
        ).aggregate(total=models.Sum("amount"))["total"] or Decimal("0.00")

        reimbursements = self.reimbursements.aggregate(
            total=models.Sum("amount")
        )["total"] or Decimal("0.00")

        self.closing_balance = self.opening_balance + reimbursements - expenses
        self.save(update_fields=["closing_balance"])
        return self.closing_balance

    @property
    def total_expenses(self):
        return self.entries.filter(
            entry_type=PettyCashEntry.EntryType.EXPENSE
        ).aggregate(total=models.Sum("amount"))["total"] or Decimal("0.00")

    @property
    def total_reimbursements(self):
        return self.reimbursements.aggregate(total=models.Sum("amount"))[
            "total"
        ] or Decimal("0.00")

    @property
    def available_balance(self):
        """What is actually in the tin right now.

        The same arithmetic as closing_balance. They are one number viewed from
        two ends of the month: mid-month the operator calls it "what's left",
        and on the 31st the accountant calls it "what closed".
        """
        return self.closing_balance


class PettyCashEntry(models.Model):
    """Money out of the tin — and, for reimbursement rows, the paperwork
    trail. `entry_type` exists because the spec asks for it, but a
    reimbursement that moves the float is a PettyCashReimbursement; see below.
    """

    class Category(models.TextChoices):
        FOOD = "food", "Food"
        TRANSPORT = "transport", "Transport"
        OFFICE = "office", "Office"
        UTILITIES = "utilities", "Utilities"
        MAINTENANCE = "maintenance", "Maintenance"
        OTHER = "other", "Other"

    class EntryType(models.TextChoices):
        EXPENSE = "expense", "Expense"
        REIMBURSEMENT = "reimbursement", "Reimbursement"

    fund = models.ForeignKey(
        PettyCashFund,
        on_delete=models.CASCADE,
        related_name="entries",
    )
    date = models.DateField()
    description = models.CharField(max_length=500)
    category = models.CharField(max_length=20, choices=Category.choices)
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    entry_type = models.CharField(
        max_length=20, choices=EntryType.choices, default=EntryType.EXPENSE
    )
    receipt_no = models.CharField(max_length=100, blank=True)
    added_by = models.ForeignKey(
        User,
        on_delete=models.PROTECT,
        related_name="petty_cash_entries",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    edit_reason = models.CharField(max_length=500, blank=True)
    edit_date = models.DateField(null=True, blank=True)

    class Meta:
        verbose_name_plural = "petty cash entries"
        ordering = ["-date", "-id"]

    def __str__(self):
        return f"{self.get_category_display()} {self.amount} on {self.date}"


class PettyCashReimbursement(models.Model):
    """Money into the tin: the float being topped up.

    A separate model from PettyCashEntry for the same reason BillSettlement is
    separate from Payment — the two answer different questions. An expense is
    money spent and needs a category and a receipt; a top-up is the float being
    restored and needs to know who handed it over. Netting them into one table
    would mean half the columns were always blank.
    """

    fund = models.ForeignKey(
        PettyCashFund,
        on_delete=models.CASCADE,
        related_name="reimbursements",
    )
    date = models.DateField()
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    reason = models.CharField(max_length=500)
    given_by = models.CharField(max_length=150)
    added_by = models.ForeignKey(
        User,
        on_delete=models.PROTECT,
        related_name="petty_cash_reimbursements",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    edit_reason = models.CharField(max_length=500, blank=True)
    edit_date = models.DateField(null=True, blank=True)

    class Meta:
        ordering = ["-date", "-id"]

    def __str__(self):
        return f"Reimbursement {self.amount} from {self.given_by}"


class MaterialSupplier(models.Model):
    """Who raw material is bought from.

    Deliberately not Customer(is_supplier=True): that model carries a balance,
    a credit limit and a ledger, and material purchasing settles none of them.
    Pointing at it would put purchases the ledger cannot see against an account
    the ledger reports on.
    """

    name = models.CharField(max_length=150)
    phone = models.CharField(max_length=30, blank=True)
    address = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class Material(models.Model):
    """Raw material bought by weight or count.

    Not Product: a Product is finished goods with a stock level that bills move.
    Material never touches Product.qty — weighing in a delivery of resin is not
    a sale of anything, and the two must not share a shelf.
    """

    class Unit(models.TextChoices):
        KG = "kg", "kg"
        G = "g", "g"
        L = "l", "L"
        ML = "ml", "mL"
        M = "m", "m"
        PIECE = "piece", "piece"

    name = models.CharField(max_length=150)
    unit = models.CharField(max_length=10, choices=Unit.choices, default=Unit.KG)
    default_unit_price = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return f"{self.name} ({self.get_unit_display()})"


class MaterialPurchase(models.Model):
    """A delivery of raw material, ordered on paper and then weighed in.

    status is cached from the items rather than set by hand — see
    `refresh_status`. No stock and no balance move anywhere: this module records
    what arrived and whether it matched the invoice, and nothing else.
    """

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        PARTIALLY_WEIGHED = "partially_weighed", "Partially Weighed"
        FULLY_WEIGHED = "fully_weighed", "Fully Weighed"

    supplier = models.ForeignKey(
        MaterialSupplier,
        on_delete=models.PROTECT,
        related_name="purchases",
    )
    purchase_date = models.DateField()
    invoice_no = models.CharField(max_length=100, blank=True)
    total_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.PENDING
    )
    notes = models.TextField(blank=True)
    created_by = models.ForeignKey(
        User,
        on_delete=models.PROTECT,
        related_name="material_purchases",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    edit_reason = models.CharField(max_length=500, blank=True)
    edit_date = models.DateField(null=True, blank=True)

    class Meta:
        ordering = ["-purchase_date", "-id"]

    def __str__(self):
        return f"Purchase #{self.pk} · {self.supplier} · {self.total_amount}"

    def refresh_status(self):
        """Recompute status and total from the items, and save if either moved.

        Nothing weighed yet is 'pending' even if items exist, which is why this
        counts weighed items rather than asking whether any weigh entry exists:
        a purchase with a 0kg entry recorded against it has still had nothing
        arrive.

        A purchase with no items at all stays pending — 'fully weighed' for an
        empty delivery would be true and useless, and would show a green badge
        for a purchase nobody has entered yet.
        """
        items = list(self.items.all())
        total = sum((item.line_total for item in items), Decimal("0.00"))

        if not items:
            status = self.Status.PENDING
        elif all(item.is_weighed for item in items):
            status = self.Status.FULLY_WEIGHED
        elif any(item.weighed_qty > 0 for item in items):
            status = self.Status.PARTIALLY_WEIGHED
        else:
            status = self.Status.PENDING

        if status != self.status or total != self.total_amount:
            self.status = status
            self.total_amount = total
            self.save(update_fields=["status", "total_amount"])
        return status


class MaterialPurchaseItem(models.Model):
    """One material on a purchase: how much was ordered, how much turned up."""

    purchase = models.ForeignKey(
        MaterialPurchase,
        on_delete=models.CASCADE,
        related_name="items",
    )
    material = models.ForeignKey(
        Material,
        on_delete=models.PROTECT,
        related_name="purchase_items",
    )
    ordered_qty = models.DecimalField(max_digits=12, decimal_places=3)
    unit_price = models.DecimalField(max_digits=12, decimal_places=2)
    line_total = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    # Cached sum of this item's weigh entries, kept by `recalculate_weighed`.
    weighed_qty = models.DecimalField(max_digits=12, decimal_places=3, default=0)
    is_weighed = models.BooleanField(default=False)

    class Meta:
        ordering = ["id"]

    def __str__(self):
        return f"{self.material} · {self.weighed_qty}/{self.ordered_qty}"

    @property
    def remaining_qty(self):
        """How much of the order has yet to be weighed in.

        Floored at zero: a delivery can weigh over the order — 500kg ordered,
        505kg on the scale — and an item cannot be owed less than nothing.
        """
        remaining = self.ordered_qty - self.weighed_qty
        return remaining if remaining > 0 else Decimal("0.000")

    @property
    def weigh_percent(self):
        """Weighed share of the order, 0–100, for the progress bar.

        Capped at 100 so an overweight delivery cannot render a bar that runs
        out of its own track.
        """
        if not self.ordered_qty:
            return 0
        percent = (self.weighed_qty / self.ordered_qty) * 100
        return min(int(percent), 100)

    def recalculate_weighed(self):
        """Rewrite weighed_qty and is_weighed from this item's weigh entries.

        A full recount rather than a delta, for the reason PettyCashFund gives:
        an edited or deleted entry cannot leave the total out by its own
        difference if the total is never told about the difference.
        """
        total = self.weigh_entries.aggregate(total=models.Sum("weighed_qty"))[
            "total"
        ] or Decimal("0.000")

        self.weighed_qty = total
        self.is_weighed = total >= self.ordered_qty
        self.save(update_fields=["weighed_qty", "is_weighed"])
        return total


class MaterialWeighEntry(models.Model):
    """One trip to the scale.

    A delivery is weighed in instalments — 500kg ordered, then 100kg, 150kg and
    250kg across three entries — so this is a log, not a single figure on the
    item. The item's weighed_qty is the cached sum of these.
    """

    purchase_item = models.ForeignKey(
        MaterialPurchaseItem,
        on_delete=models.CASCADE,
        related_name="weigh_entries",
    )
    weigh_date = models.DateField()
    weighed_qty = models.DecimalField(max_digits=12, decimal_places=3)
    # The person at the scale, as free text rather than a User FK: the checker
    # is usually a store hand who has no account in this system, and the whole
    # point of the column is to record who to ask about a short delivery.
    checked_by = models.CharField(max_length=150)
    submitted_by = models.ForeignKey(
        User,
        on_delete=models.PROTECT,
        related_name="material_weigh_entries",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name_plural = "material weigh entries"
        ordering = ["-weigh_date", "-id"]

    def __str__(self):
        return f"{self.weighed_qty} on {self.weigh_date} · checked by {self.checked_by}"


class Vehicle(models.Model):
    name = models.CharField(max_length=150)
    registration_no = models.CharField(max_length=50, blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class Rider(models.Model):
    name = models.CharField(max_length=150)
    phone = models.CharField(max_length=30, blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class VehicleTrip(models.Model):
    """One journey. `km` is this trip alone, not an odometer reading.

    Storing the leg rather than the odometer is what makes a trip editable and
    deletable on its own: odometer readings only mean anything in sequence, so
    correcting one in the middle would require rewriting every reading after it.
    A month's total is the sum of its legs.
    """

    vehicle = models.ForeignKey(
        Vehicle,
        on_delete=models.PROTECT,
        related_name="trips",
    )
    rider = models.ForeignKey(
        Rider,
        on_delete=models.PROTECT,
        related_name="trips",
    )
    trip_date = models.DateField()
    from_location = models.CharField(max_length=255)
    to_location = models.CharField(max_length=255)
    km = models.DecimalField(max_digits=10, decimal_places=2)
    purpose = models.CharField(max_length=500, blank=True)
    added_by = models.ForeignKey(
        User,
        on_delete=models.PROTECT,
        related_name="vehicle_trips",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-trip_date", "-id"]

    def __str__(self):
        return f"{self.vehicle} · {self.km}km on {self.trip_date}"


class ReferenceCounter(models.Model):
    """A number that only ever goes up, per `key`.

    Exists because the obvious way to number quotations — one past the highest
    reference in the table — hands the same number out twice. Delete the newest
    quotation and the highest drops back, so the next one issued reuses the
    reference the customer is still holding on paper. Counting rows has the same
    hole. Nothing derived from the rows can be safe, because the rows can go
    away; the count has to outlive them.
    """

    key = models.CharField(max_length=50, unique=True)
    last_value = models.PositiveIntegerField(default=0)

    def __str__(self):
        return f"{self.key} = {self.last_value}"

    @classmethod
    def next_value(cls, key):
        """Claim and return the next number for `key`.

        select_for_update holds the row for the rest of the transaction, so two
        people saving at once queue up instead of both reading the same value
        and both claiming it.
        """
        with transaction.atomic():
            counter, _ = cls.objects.select_for_update().get_or_create(key=key)
            counter.last_value = F("last_value") + 1
            counter.save(update_fields=["last_value"])
            # save() left last_value as the F() expression rather than a number;
            # only the database knows what it resolved to.
            counter.refresh_from_db(fields=["last_value"])
            return counter.last_value


class Order(models.Model):
    """A quotation. Nothing here moves money or stock.

    Not a Bill with a status: a bill takes stock off the shelf and posts to a
    customer's balance the moment it is written, and a quotation must do
    neither — the whole point is that it is a price on paper the customer has
    not agreed to yet. Sharing the model would mean every stock and ledger
    query in the system had to remember to exclude quotations, and the first one
    that forgot would sell goods nobody had ordered.
    """

    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        SENT = "sent", "Sent"
        CONFIRMED = "confirmed", "Confirmed"
        CANCELLED = "cancelled", "Cancelled"

    # Null for a walk-in quotation, where customer_name is the only record of
    # who asked. Unlike Bill there is no holding account to fall back on,
    # because there is no ledger here that has to balance.
    customer = models.ForeignKey(
        Customer,
        on_delete=models.PROTECT,
        related_name="orders",
        null=True,
        blank=True,
    )
    customer_name = models.CharField(max_length=255, blank=True)
    order_date = models.DateField()
    valid_until = models.DateField(null=True, blank=True)
    reference_no = models.CharField(max_length=20, unique=True, blank=True)
    notes = models.TextField(blank=True)
    discount_amount = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    discount_reason = models.CharField(max_length=255, blank=True)
    delivery_charge = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    subtotal = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    total_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.DRAFT
    )
    created_by = models.ForeignKey(
        User,
        on_delete=models.PROTECT,
        related_name="orders",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    edit_reason = models.CharField(max_length=500, blank=True)
    edit_date = models.DateField(null=True, blank=True)

    class Meta:
        ordering = ["-order_date", "-id"]

    def __str__(self):
        return f"{self.reference_no} · {self.display_customer}"

    @property
    def display_customer(self):
        """Who the quotation is for, however it was addressed."""
        if self.customer_id:
            return self.customer.name
        return self.customer_name or "Walk-in"

    #: What ReferenceCounter counts for quotations.
    REFERENCE_KEY = "order"

    def save(self, *args, **kwargs):
        """Assign the next reference number to a new quotation.

        The number comes from ReferenceCounter, which survives deletion, rather
        than from the highest reference in this table, which does not — see that
        model for why the obvious approach hands the same number out twice.

        This means references have gaps: delete ORD-0002 and the sequence goes
        0001, 0003. That is the intended trade. A gap is a quotation that was
        thrown away, which is answerable; a repeat is two different quotations
        both called ORD-0002 in a customer's inbox, which is not.
        """
        if not self.reference_no:
            nxt = ReferenceCounter.next_value(self.REFERENCE_KEY)
            self.reference_no = f"ORD-{nxt:04d}"
        super().save(*args, **kwargs)

    def recalculate(self):
        """Rewrite the totals from the items."""
        self.subtotal = self.items.aggregate(total=models.Sum("line_total"))[
            "total"
        ] or Decimal("0.00")
        self.total_amount = self.subtotal + self.delivery_charge - self.discount_amount
        self.save(update_fields=["subtotal", "total_amount"])
        return self.total_amount


class OrderItem(models.Model):
    order = models.ForeignKey(
        Order,
        on_delete=models.CASCADE,
        related_name="items",
    )
    product = models.ForeignKey(
        Product,
        on_delete=models.PROTECT,
        related_name="order_items",
    )
    qty = models.DecimalField(max_digits=12, decimal_places=3)
    unit_price = models.DecimalField(max_digits=12, decimal_places=2)
    line_total = models.DecimalField(max_digits=12, decimal_places=2)

    class Meta:
        ordering = ["id"]

    def __str__(self):
        return f"{self.product} × {self.qty} = {self.line_total}"


class ProductionEntry(models.Model):
    product = models.ForeignKey(
        Product,
        on_delete=models.PROTECT,
        related_name="production_entries",
    )
    production_date = models.DateField()
    qty_produced = models.DecimalField(max_digits=12, decimal_places=3)
    reason = models.CharField(max_length=500)

    # A snapshot of the shelf either side of this entry, kept because
    # Product.qty is a single running number: once a later sale moves it, there
    # is no way to work out what production found or left behind.
    #
    # Only ever equal to Product.qty at the moment the entry was written. A
    # sale afterwards moves the product on and leaves these where they were,
    # which is the point of them.
    stock_before = models.DecimalField(max_digits=12, decimal_places=3, default=0)
    stock_after = models.DecimalField(max_digits=12, decimal_places=3, default=0)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name_plural = "production entries"
        ordering = ["-production_date", "-id"]

    def __str__(self):
        return f"{self.product} × {self.qty_produced} on {self.production_date}"


class StockAdjustment(models.Model):
    """A manual correction to a product's on-hand stock.

    Not a ProductionEntry: that model models real manufacture (qty > 0) and
    the stock ledger draws it under a "Production" heading. An adjustment is
    a *correction* — someone counted the shelf and it was ten short, or
    twenty were damaged and thrown out — and it can go either way. Sharing
    the model would blur the difference between "we made this" and "we
    reconciled to this", which is exactly the distinction the operator
    reaches for when they audit the ledger later.

    `qty` is signed: positive adds to the shelf, negative removes from it.
    stock_before / stock_after are snapshots kept for the same reason
    ProductionEntry keeps them — once a later movement touches Product.qty
    there is no way to work out what this row found or left behind.
    """

    product = models.ForeignKey(
        Product,
        on_delete=models.PROTECT,
        related_name="stock_adjustments",
    )
    adjustment_date = models.DateField()
    # Signed. Store `qty` and read the sign — a magnitude with a separate
    # direction flag would let a "-100 add" and a "+100 remove" both exist
    # and neither the ledger nor the operator could tell them apart.
    qty = models.DecimalField(max_digits=12, decimal_places=3)
    reason = models.CharField(max_length=500)

    stock_before = models.DecimalField(max_digits=12, decimal_places=3, default=0)
    stock_after = models.DecimalField(max_digits=12, decimal_places=3, default=0)

    adjusted_by = models.ForeignKey(
        User,
        on_delete=models.PROTECT,
        related_name="stock_adjustments",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-adjustment_date", "-id"]

    def __str__(self):
        sign = "+" if self.qty >= 0 else ""
        return f"{self.product} · {sign}{self.qty} on {self.adjustment_date}"


class Machine(models.Model):
    """A production machine on the floor.

    `is_active` here is permanent — a decommissioned machine that will never
    run again. Whether it ran on any given day is a separate question,
    answered by the DailyMachineRun row for that (date, machine).
    """

    name = models.CharField(max_length=150, unique=True)
    is_active = models.BooleanField(default=True)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class DailyMachineRun(models.Model):
    """One machine's story for one day.

    Exactly one row per (run_date, machine), enforced by unique_together.
    A day where a machine did not run is a row with status=NOT_WORKING and
    the operator/product left blank — the absence of a row means the
    operator hasn't logged that machine yet, which is different from "the
    machine sat idle".
    """

    class Status(models.TextChoices):
        RUNNING = "running", "Running"
        NOT_WORKING = "not_working", "Not working"

    run_date = models.DateField()
    machine = models.ForeignKey(
        Machine,
        on_delete=models.PROTECT,
        related_name="daily_runs",
    )
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.RUNNING
    )
    operator = models.CharField(max_length=150, blank=True)
    # Nullable: a machine that isn't running doesn't have a product on it.
    product = models.ForeignKey(
        Product,
        on_delete=models.PROTECT,
        related_name="daily_runs",
        null=True,
        blank=True,
    )
    notes = models.CharField(max_length=500, blank=True)
    logged_by = models.ForeignKey(
        User,
        on_delete=models.PROTECT,
        related_name="daily_machine_runs",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ("run_date", "machine")
        ordering = ["-run_date", "machine__name"]

    def __str__(self):
        return f"{self.machine} · {self.run_date} · {self.get_status_display()}"


class DailyOtherWork(models.Model):
    """Everything else that happened on the floor that day.

    One row per date (enforced by unique). Fields are free text because the
    operator's shorthand ("Nimal drove to Kandy, Kamal did resin mixing")
    is what the record is for; making them structured would ask the
    operator to standardise language that only they need to read back.
    """

    run_date = models.DateField(unique=True)
    driver = models.CharField(max_length=500, blank=True)
    material_supply = models.CharField(max_length=500, blank=True)
    material_mixing = models.CharField(max_length=500, blank=True)
    other = models.TextField(blank=True)

    logged_by = models.ForeignKey(
        User,
        on_delete=models.PROTECT,
        related_name="daily_other_works",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    edit_reason = models.CharField(max_length=500, blank=True)
    edit_date = models.DateField(null=True, blank=True)

    class Meta:
        ordering = ["-run_date"]

    def __str__(self):
        return f"Other work · {self.run_date}"
