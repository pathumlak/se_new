import json
from decimal import Decimal, InvalidOperation

from django import forms
from django.db.models import Q
from django.utils import timezone
from django.utils.dateparse import parse_date

from .models import (
    CashDrawer,
    Category,
    Cheque,
    Customer,
    CustomerBalanceAdjustment,
    DailyOtherWork,
    Machine,
    Material,
    MaterialPurchase,
    MaterialSupplier,
    MaterialWeighEntry,
    Order,
    Payment,
    PettyCashEntry,
    PettyCashReimbursement,
    Product,
    ProductionEntry,
    Rider,
    StockAdjustment,
    User,
    Vehicle,
    VehicleTrip,
)

INPUT_CLASSES = (
    "block w-full rounded-lg border border-slate-300 px-3 py-2 text-sm text-slate-900 "
    "placeholder:text-slate-400 shadow-sm "
    "focus:border-slate-900 focus:outline-none focus:ring-1 focus:ring-slate-900"
)

SELECT_CLASSES = INPUT_CLASSES + " bg-white"

CHECKBOX_CLASSES = (
    "h-4 w-4 rounded border-slate-300 text-slate-900 "
    "focus:ring-1 focus:ring-slate-900"
)


class UserCreateForm(forms.ModelForm):
    """Create a staff account.

    No password field: there is no self-registration, so the first password is
    generated and shown to the super admin once, rather than chosen by whoever
    happens to be filling the form in.
    """

    class Meta:
        model = User
        fields = ["username", "first_name", "last_name", "email", "role", "is_active"]
        labels = {
            "first_name": "First name",
            "last_name": "Last name",
            "is_active": "Active",
        }
        widgets = {
            "username": forms.TextInput(
                attrs={
                    "class": INPUT_CLASSES,
                    "placeholder": "e.g. nimal",
                    "autofocus": True,
                    "autocapitalize": "none",
                }
            ),
            "first_name": forms.TextInput(
                attrs={"class": INPUT_CLASSES, "placeholder": "e.g. Nimal"}
            ),
            "last_name": forms.TextInput(
                attrs={"class": INPUT_CLASSES, "placeholder": "e.g. Perera"}
            ),
            "email": forms.EmailInput(
                attrs={"class": INPUT_CLASSES, "placeholder": "e.g. nimal@senovka.lk"}
            ),
            "role": forms.Select(attrs={"class": SELECT_CLASSES}),
            "is_active": forms.CheckboxInput(attrs={"class": CHECKBOX_CLASSES}),
        }
        help_texts = {
            "username": "Used to sign in. It cannot be changed later.",
            "email": "Leave blank if you don't have one.",
            "is_active": "An inactive account cannot sign in.",
        }

    def clean_username(self):
        """Stored lower-cased. Django's username lookup is case-sensitive, so
        'Nimal' and 'nimal' would otherwise be two accounts that look like one.
        """
        username = self.cleaned_data["username"].strip().lower()
        if User.objects.filter(username__iexact=username).exists():
            raise forms.ValidationError("That username is taken.")
        return username


class UserEditForm(forms.ModelForm):
    """Edit a staff account.

    `username` is absent: it identifies the account, and every record already
    written points at this row — renaming it would rewrite history.

    `role` and `is_active` are *removed from the form* when a super admin edits
    their own account, not just disabled in the template. A disabled input is
    still a field the POST can carry, so hiding alone would let a super admin
    demote or lock themselves out by hand. Dropping them means their POST
    cannot reach those columns at all.
    """

    class Meta:
        model = User
        fields = ["first_name", "last_name", "email", "role", "is_active"]
        labels = {
            "first_name": "First name",
            "last_name": "Last name",
            "is_active": "Active",
        }
        widgets = {
            "first_name": forms.TextInput(
                attrs={"class": INPUT_CLASSES, "autofocus": True}
            ),
            "last_name": forms.TextInput(attrs={"class": INPUT_CLASSES}),
            "email": forms.EmailInput(attrs={"class": INPUT_CLASSES}),
            "role": forms.Select(attrs={"class": SELECT_CLASSES}),
            "is_active": forms.CheckboxInput(attrs={"class": CHECKBOX_CLASSES}),
        }
        help_texts = {
            "email": "Leave blank if you don't have one.",
            "is_active": "An inactive account cannot sign in. Records they "
            "already created are not affected.",
        }

    def __init__(self, *args, is_self=False, **kwargs):
        super().__init__(*args, **kwargs)
        self.is_self = is_self
        if is_self:
            del self.fields["role"]
            del self.fields["is_active"]


class CategoryForm(forms.ModelForm):
    class Meta:
        model = Category
        fields = ["name", "description"]
        widgets = {
            "name": forms.TextInput(
                attrs={
                    "class": INPUT_CLASSES,
                    "placeholder": "e.g. PVC Fittings",
                    "autofocus": True,
                }
            ),
            "description": forms.Textarea(
                attrs={
                    "class": INPUT_CLASSES,
                    "rows": 4,
                    "placeholder": "What belongs in this category?",
                }
            ),
        }

    def clean_name(self):
        """Collapse surrounding whitespace so ' PVC ' can't slip past the
        unique constraint as a near-duplicate of 'PVC'."""
        return self.cleaned_data["name"].strip()


class ProductForm(forms.ModelForm):
    """Create/edit a product.

    `qty` is deliberately absent: stock starts at 0 and only moves through
    production entries and supplier bills, so it must never be typed in here.
    """

    class Meta:
        model = Product
        fields = ["name", "size", "category", "default_price", "is_active"]
        widgets = {
            "name": forms.TextInput(
                attrs={
                    "class": INPUT_CLASSES,
                    "placeholder": "e.g. Water Tank",
                    "autofocus": True,
                }
            ),
            "size": forms.TextInput(
                attrs={"class": INPUT_CLASSES, "placeholder": "e.g. 1000L"}
            ),
            "category": forms.Select(attrs={"class": SELECT_CLASSES}),
            "default_price": forms.NumberInput(
                attrs={
                    "class": INPUT_CLASSES,
                    "step": "0.01",
                    "min": "0",
                    "placeholder": "0.00",
                }
            ),
            "is_active": forms.CheckboxInput(attrs={"class": CHECKBOX_CLASSES}),
        }
        help_texts = {
            "size": "Leave blank if this product comes in one size only.",
            "default_price": "Used unless the customer has a negotiated price.",
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["category"].empty_label = "Select a category"

    def clean_name(self):
        return self.cleaned_data["name"].strip()

    def clean_size(self):
        return self.cleaned_data["size"].strip()

    def clean_default_price(self):
        price = self.cleaned_data["default_price"]
        if price < 0:
            raise forms.ValidationError("Price cannot be negative.")
        return price

    def clean(self):
        """Name alone may repeat across sizes, but the same name *and* size is
        a duplicate — bill lines would become impossible to tell apart."""
        cleaned = super().clean()
        name = cleaned.get("name")
        size = cleaned.get("size")

        if name is None or size is None:
            return cleaned

        clash = Product.objects.filter(name__iexact=name, size__iexact=size)
        if self.instance.pk:
            clash = clash.exclude(pk=self.instance.pk)

        if clash.exists():
            label = f"{name} {size}".strip()
            raise forms.ValidationError(f"'{label}' already exists.")

        return cleaned


class CustomerForm(forms.ModelForm):
    """Create/edit a customer.

    `balance` is editable directly here — positive means we owe the customer,
    negative means the customer owes us. Managers and super admins both may
    set it. Bills, payments and cheques still move the balance as they always
    did; this field just lets the operator seed an opening figure and correct
    it later without going via a separate adjustment flow.

    `credit_limit` is *removed from the form* for managers, not just hidden in
    the template. A field hidden in HTML is still a field the POST can carry,
    so hiding alone would let a manager set any limit by hand. Dropping it
    means a manager's POST cannot reach the column at all: on create it takes
    the model default, and on edit the stored limit is left untouched.
    """

    class Meta:
        model = Customer
        fields = [
            "name",
            "phone",
            "address",
            "balance",
            "credit_limit",
            "is_supplier",
            "is_active",
        ]
        labels = {
            "balance": "Opening balance",
        }
        widgets = {
            "name": forms.TextInput(
                attrs={
                    "class": INPUT_CLASSES,
                    "placeholder": "e.g. Nimal Stores",
                    "autofocus": True,
                }
            ),
            "phone": forms.TextInput(
                attrs={"class": INPUT_CLASSES, "placeholder": "e.g. 077 123 4567"}
            ),
            "address": forms.Textarea(
                attrs={
                    "class": INPUT_CLASSES,
                    "rows": 3,
                    "placeholder": "Billing or delivery address",
                }
            ),
            "balance": forms.NumberInput(
                attrs={
                    "class": INPUT_CLASSES,
                    "step": "0.01",
                    "placeholder": "0.00",
                }
            ),
            "credit_limit": forms.NumberInput(
                attrs={
                    "class": INPUT_CLASSES,
                    "step": "0.01",
                    "min": "0",
                    "placeholder": "0.00",
                }
            ),
            "is_supplier": forms.CheckboxInput(attrs={"class": CHECKBOX_CLASSES}),
            "is_active": forms.CheckboxInput(attrs={"class": CHECKBOX_CLASSES}),
        }
        help_texts = {
            "phone": "Leave blank if you don't have one.",
            "address": "Leave blank if you don't have one.",
            "balance": "Positive = we owe the customer. Negative = customer owes us. Leave 0 for a fresh account.",
            "credit_limit": "How much this customer may owe before new credit sales should be refused.",
        }

    def __init__(self, *args, is_super_admin=False, **kwargs):
        super().__init__(*args, **kwargs)
        if not is_super_admin:
            del self.fields["credit_limit"]

    def clean_name(self):
        """Collapse surrounding whitespace so ' Nimal ' and 'Nimal' don't read
        as two different accounts in the ledger."""
        return self.cleaned_data["name"].strip()

    def clean_phone(self):
        return self.cleaned_data["phone"].strip()

    def clean_credit_limit(self):
        # Only reachable for a super admin; managers have no such field.
        limit = self.cleaned_data["credit_limit"]
        if limit < 0:
            raise forms.ValidationError("Credit limit cannot be negative.")
        return limit


class ProductionEntryForm(forms.ModelForm):
    """Correct one production entry.

    `product` is absent: it is what was made, and pointing the entry at a
    different product is not a correction — it's two corrections, and the stock
    move would have to reverse one shelf and add to another. Delete and re-enter
    instead.

    stock_before and stock_after are snapshots the view maintains, never typed.
    """

    class Meta:
        model = ProductionEntry
        fields = ["production_date", "qty_produced", "reason"]
        labels = {
            "production_date": "Production date",
            "qty_produced": "Quantity produced",
            "reason": "Reason",
        }
        widgets = {
            # format pinned: a <input type=date> only pre-fills from an ISO
            # value, and the localised default would render the stored date as
            # an empty box.
            "production_date": forms.DateInput(
                format="%Y-%m-%d",
                attrs={"class": INPUT_CLASSES, "type": "date"},
            ),
            "qty_produced": forms.NumberInput(
                attrs={
                    "class": INPUT_CLASSES,
                    "step": "0.001",
                    "min": "0",
                    "autofocus": True,
                }
            ),
            "reason": forms.TextInput(
                attrs={
                    "class": INPUT_CLASSES,
                    "placeholder": "e.g. Morning production run",
                    "maxlength": 500,
                }
            ),
        }
        help_texts = {
            "reason": "What this production was — e.g. Morning production run, "
            "Evening batch, Recount correction.",
        }
        error_messages = {
            "reason": {"required": "Give a reason for this production."},
        }

    def clean_qty_produced(self):
        qty = self.cleaned_data["qty_produced"]
        if qty <= 0:
            raise forms.ValidationError(
                "Quantity must be above 0. Delete the entry instead."
            )
        return qty

    def clean_production_date(self):
        production_date = self.cleaned_data["production_date"]
        # Mirrors _save_production: production can be recorded late, never early.
        if production_date > timezone.localdate():
            raise forms.ValidationError("Production can't be dated in the future.")
        return production_date

    def clean_reason(self):
        """A reason of spaces is no reason. CharField already strips, so this
        only has to reject what stripping leaves empty."""
        reason = self.cleaned_data["reason"].strip()
        if not reason:
            raise forms.ValidationError("Give a reason for this production.")
        return reason


class SupplierQuickForm(forms.ModelForm):
    """Create a supplier from inside the supplier-bill form.

    `is_supplier` is forced rather than offered: this form only exists on a
    page that is about to bill the party as a supplier.
    """

    class Meta:
        model = Customer
        fields = ["name", "phone", "address"]
        widgets = {
            "name": forms.TextInput(
                attrs={"class": INPUT_CLASSES, "placeholder": "e.g. Lanka Polymers"}
            ),
            "phone": forms.TextInput(
                attrs={"class": INPUT_CLASSES, "placeholder": "e.g. 077 123 4567"}
            ),
            "address": forms.Textarea(attrs={"class": INPUT_CLASSES, "rows": 2}),
        }

    def clean_name(self):
        return self.cleaned_data["name"].strip()

    def clean_phone(self):
        return self.cleaned_data["phone"].strip()

    def save(self, commit=True):
        supplier = super().save(commit=False)
        supplier.is_supplier = True
        if commit:
            supplier.save()
        return supplier


class ProductQuickForm(ProductForm):
    """Create a product from inside the supplier-bill form.

    Subclasses ProductForm to inherit its duplicate name+size check — a
    shortcut on this page must not be a way around it. `is_active` is dropped
    and forced on: a product being received into stock is one being sold.
    """

    class Meta(ProductForm.Meta):
        fields = ["name", "size", "category", "default_price"]

    def save(self, commit=True):
        product = super().save(commit=False)
        product.is_active = True
        if commit:
            product.save()
        return product


class CashDrawerOutForm(forms.ModelForm):
    """Record cash leaving the drawer by hand.

    Everything here is money going out, so `txn_type` is not a field — it is
    always OUT. Money coming *in* is recorded by saving a bill, never typed.

    `kind` is not stored: CashDrawer has no account column, so which account a
    transfer went to survives only in the reason text. The per-account totals
    on the page are read from CashTransfer instead, and so do not count these.
    """

    KIND_CHOICES = [
        ("withdrawal", "Owner Withdrawal"),
        ("senovka", "Transfer to Senovka Account"),
        ("dinusha", "Transfer to Dinusha Account"),
    ]

    kind = forms.ChoiceField(
        choices=KIND_CHOICES,
        label="Type",
        widget=forms.Select(attrs={"class": SELECT_CLASSES}),
    )

    class Meta:
        model = CashDrawer
        fields = ["txn_date", "amount", "reason"]
        labels = {"txn_date": "Date"}
        widgets = {
            "txn_date": forms.DateInput(attrs={"class": INPUT_CLASSES, "type": "date"}),
            "amount": forms.NumberInput(
                attrs={
                    "class": INPUT_CLASSES,
                    "step": "0.01",
                    "min": "0",
                    "placeholder": "0.00",
                }
            ),
            "reason": forms.TextInput(
                attrs={"class": INPUT_CLASSES, "placeholder": "e.g. banked at BOC Galle"}
            ),
        }

    def __init__(self, *args, drawer_balance=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.drawer_balance = drawer_balance

    def clean_reason(self):
        return self.cleaned_data["reason"].strip()

    def clean_amount(self):
        amount = self.cleaned_data["amount"]
        if amount <= 0:
            raise forms.ValidationError("Amount must be above 0.")

        # Cash that isn't in the drawer can't leave it. Without this the page
        # would happily report a negative pile of banknotes.
        if self.drawer_balance is not None and amount > self.drawer_balance:
            raise forms.ValidationError(
                f"Only {self.drawer_balance:,.2f} is in the drawer."
            )
        return amount

    def save(self, commit=True):
        entry = super().save(commit=False)
        entry.txn_type = CashDrawer.TxnType.OUT

        # The destination lives in the text or nowhere, so it goes in the text.
        label = dict(self.KIND_CHOICES)[self.cleaned_data["kind"]]
        note = self.cleaned_data.get("reason", "")
        entry.reason = (f"{label} — {note}" if note else label)[:255]

        if commit:
            entry.save()
        return entry


class CashDrawerEditForm(forms.ModelForm):
    """Correct a manual drawer entry.

    Only ever reaches an entry with no bill: one auto-written by a bill belongs
    to that bill's payment, and correcting it here would put the drawer out of
    step with the bill it came from. The view refuses those; this form never
    sees one.

    `txn_type` *is* editable, unlike on CashDrawerOutForm where everything is
    money going out — a withdrawal keyed as a transfer is exactly the kind of
    mistake this page exists to fix.

    No balance is reversed on save: the running balance on the list is summed
    from the rows themselves on every render, so a corrected row is simply
    counted differently next time.
    """

    edit_reason = forms.CharField(
        label="Reason for Edit",
        max_length=500,
        error_messages={
            "required": "Give a reason for this edit.",
            "max_length": "Keep the reason under 500 characters.",
        },
        widget=forms.TextInput(
            attrs={
                "class": INPUT_CLASSES,
                "placeholder": "e.g. Wrong amount keyed, Wrong date",
                "maxlength": 500,
            }
        ),
    )

    class Meta:
        model = CashDrawer
        fields = ["txn_date", "txn_type", "amount", "reason", "edit_reason"]
        labels = {
            "txn_date": "Date",
            "txn_type": "Type",
            "reason": "Description",
        }
        widgets = {
            # format pinned: see ProductionEntryForm — a bound <input type=date>
            # renders empty unless the value is ISO.
            "txn_date": forms.DateInput(
                format="%Y-%m-%d",
                attrs={"class": INPUT_CLASSES, "type": "date"},
            ),
            "txn_type": forms.Select(attrs={"class": SELECT_CLASSES}),
            "amount": forms.NumberInput(
                attrs={"class": INPUT_CLASSES, "step": "0.01", "min": "0"}
            ),
            "reason": forms.TextInput(
                attrs={
                    "class": INPUT_CLASSES,
                    "placeholder": "e.g. Owner Withdrawal — school fees",
                }
            ),
        }
        help_texts = {
            "reason": "What this entry was for. Shown in the log.",
        }

    def __init__(self, *args, drawer_balance=None, **kwargs):
        super().__init__(*args, **kwargs)
        #: The drawer with this entry left out — what it would hold if this row
        #: didn't exist. An edited 'out' can't take more than that.
        self.drawer_balance = drawer_balance

    def clean_reason(self):
        return self.cleaned_data["reason"].strip()

    def clean_edit_reason(self):
        reason = self.cleaned_data["edit_reason"].strip()
        if not reason:
            raise forms.ValidationError("Give a reason for this edit.")
        return reason

    def clean_amount(self):
        amount = self.cleaned_data["amount"]
        if amount <= 0:
            raise forms.ValidationError("Amount must be above 0.")
        return amount

    def clean(self):
        """Cash that isn't in the drawer still can't leave it.

        The same rule CashDrawerOutForm applies on the way in, checked here
        against the drawer minus this entry — editing a 500 withdrawal up to
        5000 has to be judged as if the 500 had never been taken.

        In clean(), not clean_amount(): the answer depends on txn_type too, and
        field cleaning runs in field order, so txn_type isn't known yet.
        """
        cleaned = super().clean()

        amount = cleaned.get("amount")
        txn_type = cleaned.get("txn_type")
        if amount is None or txn_type is None or self.drawer_balance is None:
            return cleaned

        if txn_type != CashDrawer.TxnType.IN and amount > self.drawer_balance:
            self.add_error(
                "amount",
                f"Only {self.drawer_balance:,.2f} would be in the drawer without "
                f"this entry.",
            )

        return cleaned


class ChequeForm(forms.ModelForm):
    """Correct a cheque's details.

    `customer` and `payment` are absent: a cheque belongs to the payment that
    brought it in, and moving it to someone else's account is not a
    correction. Changing `amount` or `status` moves the customer's balance —
    the view works out by how much.
    """

    class Meta:
        model = Cheque
        fields = [
            "cheque_no",
            "bank_name",
            "branch",
            "acc_no",
            "amount",
            "received_date",
            "maturity_date",
            "status",
            "bounce_new_date",
        ]
        widgets = {
            "cheque_no": forms.TextInput(attrs={"class": INPUT_CLASSES, "autofocus": True}),
            "bank_name": forms.TextInput(
                attrs={"class": INPUT_CLASSES, "placeholder": "e.g. BOC"}
            ),
            "branch": forms.TextInput(attrs={"class": INPUT_CLASSES}),
            "acc_no": forms.TextInput(attrs={"class": INPUT_CLASSES}),
            "amount": forms.NumberInput(
                attrs={"class": INPUT_CLASSES, "step": "0.01", "min": "0"}
            ),
            "received_date": forms.DateInput(
                attrs={"class": INPUT_CLASSES, "type": "date"}
            ),
            "maturity_date": forms.DateInput(
                attrs={"class": INPUT_CLASSES, "type": "date"}
            ),
            "status": forms.Select(attrs={"class": SELECT_CLASSES}),
            "bounce_new_date": forms.DateInput(
                attrs={"class": INPUT_CLASSES, "type": "date"}
            ),
        }
        help_texts = {
            "bounce_new_date": "The date the customer agreed to re-present it.",
            "amount": "Changing this moves the customer's balance by the difference.",
        }

    def clean_cheque_no(self):
        return self.cleaned_data["cheque_no"].strip()

    def clean_bank_name(self):
        return self.cleaned_data["bank_name"].strip()

    def clean_amount(self):
        amount = self.cleaned_data["amount"]
        if amount <= 0:
            raise forms.ValidationError("Cheque amount must be above 0.")
        return amount

    def clean(self):
        cleaned = super().clean()

        received = cleaned.get("received_date")
        maturity = cleaned.get("maturity_date")
        if received and maturity and maturity < received:
            self.add_error(
                "maturity_date", "Maturity date cannot be before the received date."
            )

        # A bounced cheque without a re-presentation date is a dead end: the
        # cheque list has nothing to chase it by.
        if cleaned.get("status") == Cheque.Status.BOUNCED and not cleaned.get(
            "bounce_new_date"
        ):
            self.add_error(
                "bounce_new_date", "A bounced cheque needs a new expected date."
            )

        return cleaned


class BillEditReasonForm(forms.Form):
    """The gate in front of the bill edit form: when, and why.

    Asked before the bill is on screen rather than alongside it, so the reason
    is what the biller came to do rather than something typed to get past a
    validation error on the way out.
    """

    edit_date = forms.DateField(
        label="Edit Date",
        error_messages={
            "required": "Enter the date of this edit.",
            "invalid": "Enter a valid date.",
        },
        widget=forms.DateInput(
            format="%Y-%m-%d",
            attrs={"type": "date", "class": INPUT_CLASSES},
        ),
    )
    reason = forms.CharField(
        label="Reason for Edit",
        max_length=500,
        error_messages={
            "required": "Give a reason for this edit.",
            "max_length": "Keep the reason under 500 characters.",
        },
        widget=forms.TextInput(
            attrs={
                "class": INPUT_CLASSES,
                "placeholder": "e.g. Wrong qty entered, Price correction",
                "autofocus": True,
                "maxlength": 500,
            }
        ),
    )

    def clean_reason(self):
        """A reason of spaces is no reason. CharField already strips, so this
        only has to reject what stripping leaves empty."""
        reason = self.cleaned_data["reason"].strip()
        if not reason:
            raise forms.ValidationError("Give a reason for this edit.")
        return reason


class CustomerPriceForm(forms.Form):
    """Validates one price save from a price table's AJAX call.

    Field names mirror the POST keys the tables send. A plain Form, not a
    ModelForm: (customer, product) is unique_together, so a ModelForm would
    reject a re-price of an existing pair as a duplicate instead of updating
    it. The view resolves create-vs-update itself.
    """

    customer_id = forms.ModelChoiceField(
        queryset=Customer.objects.all(),
        error_messages={
            "required": "Missing customer.",
            "invalid_choice": "That customer no longer exists.",
        },
    )
    product_id = forms.ModelChoiceField(
        queryset=Product.objects.all(),
        error_messages={
            "required": "Missing product.",
            "invalid_choice": "That product no longer exists.",
        },
    )
    # Mirrors CustomerPrice.unit_price, so anything the column can't hold is
    # rejected with a message instead of raising at save().
    unit_price = forms.DecimalField(
        max_digits=12,
        decimal_places=2,
        min_value=Decimal("0"),
        error_messages={
            "required": "Enter a price.",
            "invalid": "Enter a valid number.",
            "min_value": "Price cannot be negative.",
            "max_decimal_places": "Use at most 2 decimal places.",
            "max_digits": "That price is too large.",
        },
    )

    def first_error(self):
        """The one message the row shows beneath its input."""
        for messages in self.errors.values():
            return messages[0]
        return "Could not save."


class CustomerBalanceAdjustmentForm(forms.ModelForm):
    """One manual balance adjustment for a customer.

    Rendered inside a modal on the customer detail page. `customer` and
    `adjusted_by` are set by the view — the caller already knows who and where
    — so this only asks for the four figures the operator supplies.
    """

    class Meta:
        model = CustomerBalanceAdjustment
        fields = ["adjustment_date", "adjustment_type", "amount", "reason"]
        labels = {
            "adjustment_date": "Adjustment date",
            "adjustment_type": "Type",
            "amount": "Amount",
            "reason": "Reason",
        }
        widgets = {
            "adjustment_date": forms.DateInput(
                format="%Y-%m-%d",
                attrs={"class": INPUT_CLASSES, "type": "date"},
            ),
            "adjustment_type": forms.RadioSelect(),
            "amount": forms.NumberInput(
                attrs={
                    "class": INPUT_CLASSES,
                    "step": "0.01",
                    "min": "0.01",
                    "placeholder": "0.00",
                }
            ),
            "reason": forms.TextInput(
                attrs={
                    "class": INPUT_CLASSES,
                    "placeholder": "e.g. Opening balance, off-book credit, correction",
                    "maxlength": 500,
                }
            ),
        }
        error_messages = {
            "reason": {"required": "Give a reason for this adjustment."},
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if not self.initial.get("adjustment_date") and not self.instance.pk:
            self.initial["adjustment_date"] = timezone.localdate()
        if not self.initial.get("adjustment_type") and not self.instance.pk:
            self.initial["adjustment_type"] = CustomerBalanceAdjustment.Type.CREDIT

    def clean_amount(self):
        amount = self.cleaned_data["amount"]
        if amount <= 0:
            raise forms.ValidationError(
                "Amount must be above 0. Switch the type instead of using a negative."
            )
        return amount

    def clean_reason(self):
        reason = self.cleaned_data["reason"].strip()
        if not reason:
            raise forms.ValidationError("Give a reason for this adjustment.")
        return reason

    def first_error(self):
        for messages in self.errors.values():
            return messages[0]
        return "Could not save."


class BillPaymentForm(forms.Form):
    """Record a follow-up payment against a bill that was left outstanding.

    Not a ModelForm: a single payment on the source bill is one Payment row
    (cash) OR one Payment + one Cheque row (cheque). The `method` field decides
    which fields are actually required, so a plain Form with per-method
    validation is simpler than layering a modelform on top.

    Bound to `bill` at __init__ so the amount can be checked against what is
    actually still owed — anything larger is refused with the exact figure, not
    a generic 'too much'.
    """

    METHOD_CHOICES = [
        (Payment.Method.CASH, "Cash"),
        (Payment.Method.CHEQUE, "Cheque"),
    ]

    method = forms.ChoiceField(
        choices=METHOD_CHOICES,
        widget=forms.RadioSelect(),
        initial=Payment.Method.CASH,
    )
    amount = forms.DecimalField(
        max_digits=12,
        decimal_places=2,
        min_value=Decimal("0.01"),
        widget=forms.NumberInput(
            attrs={
                "class": INPUT_CLASSES,
                "step": "0.01",
                "min": "0.01",
                "placeholder": "0.00",
            }
        ),
    )
    cash_account = forms.ChoiceField(
        choices=[("", "Physical drawer")] + list(Payment.Account.choices),
        required=False,
        widget=forms.Select(attrs={"class": SELECT_CLASSES}),
    )

    # Cheque-only fields. All optional at the field level; `clean` promotes
    # them to required when method='cheque'. That way a cash-only submission
    # is not tripped up by empty cheque fields the operator never touched.
    cheque_no = forms.CharField(
        max_length=50, required=False,
        widget=forms.TextInput(attrs={"class": INPUT_CLASSES}),
    )
    bank_name = forms.CharField(
        max_length=100, required=False,
        widget=forms.TextInput(attrs={"class": INPUT_CLASSES, "placeholder": "e.g. BOC"}),
    )
    branch = forms.CharField(
        max_length=100, required=False,
        widget=forms.TextInput(attrs={"class": INPUT_CLASSES}),
    )
    acc_no = forms.CharField(
        max_length=50, required=False,
        widget=forms.TextInput(attrs={"class": INPUT_CLASSES}),
    )
    received_date = forms.DateField(
        required=False,
        widget=forms.DateInput(
            format="%Y-%m-%d",
            attrs={"class": INPUT_CLASSES, "type": "date"},
        ),
    )
    maturity_date = forms.DateField(
        required=False,
        widget=forms.DateInput(
            format="%Y-%m-%d",
            attrs={"class": INPUT_CLASSES, "type": "date"},
        ),
    )

    def __init__(self, *args, bill=None, **kwargs):
        super().__init__(*args, **kwargs)
        # Bill is the reference for the remaining-balance check below. The
        # view has already refused to open this form for a walk-in, a
        # cancelled bill or a settled one — the check here is only for the
        # amount, which the operator types.
        self.bill = bill
        if not self.initial.get("received_date"):
            self.initial["received_date"] = timezone.localdate()

    def clean_amount(self):
        amount = self.cleaned_data["amount"]
        if self.bill is not None and amount > self.bill.remaining_balance:
            raise forms.ValidationError(
                f"That is more than the {self.bill.remaining_balance:,.2f} still "
                f"outstanding on this bill."
            )
        return amount

    def clean(self):
        cleaned = super().clean()
        method = cleaned.get("method")

        if method == Payment.Method.CHEQUE:
            required = {
                "cheque_no": "Cheque number is required.",
                "bank_name": "Bank name is required.",
                "received_date": "Received date is required.",
                "maturity_date": "Maturity date is required.",
            }
            for field, message in required.items():
                if not cleaned.get(field):
                    self.add_error(field, message)

            received = cleaned.get("received_date")
            maturity = cleaned.get("maturity_date")
            if received and maturity and maturity < received:
                self.add_error(
                    "maturity_date",
                    "Maturity date cannot be before the received date.",
                )

        return cleaned

    def first_error(self):
        for messages in self.errors.values():
            return messages[0]
        return "Could not save."


class CustomerSettlementForm(forms.Form):
    """Settle one lump payment against a customer's outstanding bills.

    Not a ModelForm: a settlement can produce many Payment rows across many
    Bills — the view fans out the amount FIFO across the customer's unpaid
    bills, then creates one Payment per bill via the same _record_payments
    the bill-write path uses.

    Cheques are supplied as JSON on `cheques_json` so any number of them can
    ride on the same submission; the view parses and validates each one.

    Amount rules:
      * Cash: cash > 0.
      * Cheque: at least one cheque row, each with amount > 0.
      * Mixed: both a cash amount and at least one cheque.
    A total larger than what is outstanding is allowed — the excess lands on
    the customer's balance as credit for the next bill.
    """

    METHOD_CHOICES = [
        ("cash", "Cash"),
        ("cheque", "Cheque"),
        ("mixed", "Mixed"),
    ]

    method = forms.ChoiceField(
        choices=METHOD_CHOICES,
        widget=forms.RadioSelect(),
        initial="cash",
    )
    cash_amount = forms.DecimalField(
        max_digits=12, decimal_places=2, required=False,
        min_value=Decimal("0"),
        widget=forms.NumberInput(
            attrs={"class": INPUT_CLASSES, "step": "0.01", "min": "0", "placeholder": "0.00"}
        ),
    )
    cash_account = forms.ChoiceField(
        choices=[("", "Physical drawer")] + list(Payment.Account.choices),
        required=False,
        widget=forms.Select(attrs={"class": SELECT_CLASSES}),
    )
    # JSON list of {cheque_no, bank_name, branch, acc_no, amount,
    # received_date, maturity_date}. The template's JS collects the rows into
    # this hidden field on submit — matches how _bill_form does cheque rows.
    cheques_json = forms.CharField(required=False, widget=forms.HiddenInput())

    def __init__(self, *args, customer=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.customer = customer
        self.parsed_cheques = []

    def clean(self):
        cleaned = super().clean()
        method = cleaned.get("method") or "cash"

        # ---- cheques ----
        raw = cleaned.get("cheques_json") or ""
        if raw.strip():
            try:
                items = json.loads(raw)
            except (ValueError, TypeError):
                raise forms.ValidationError("Cheque list is malformed.")
            if not isinstance(items, list):
                raise forms.ValidationError("Cheque list is malformed.")

            for index, row in enumerate(items, start=1):
                if not isinstance(row, dict):
                    raise forms.ValidationError(f"Cheque {index}: malformed row.")
                cheque = self._read_one_cheque(row, index)
                self.parsed_cheques.append(cheque)

        cash_amount = cleaned.get("cash_amount") or Decimal("0")
        cheque_total = sum((c["amount"] for c in self.parsed_cheques), Decimal("0"))

        # ---- per-method validation ----
        if method == "cash":
            if cash_amount <= 0:
                self.add_error("cash_amount", "Cash amount must be above 0.")
            if self.parsed_cheques:
                raise forms.ValidationError(
                    "Cash method — remove the cheques or switch to Mixed."
                )

        elif method == "cheque":
            if not self.parsed_cheques:
                raise forms.ValidationError("Add at least one cheque.")
            if cash_amount > 0:
                raise forms.ValidationError(
                    "Cheque method — clear the cash box or switch to Mixed."
                )

        elif method == "mixed":
            if cash_amount <= 0:
                self.add_error("cash_amount", "Mixed payment needs a cash amount above 0.")
            if not self.parsed_cheques:
                raise forms.ValidationError("Mixed payment needs at least one cheque.")

        # ---- cash account, if cash is being tendered ----
        account = cleaned.get("cash_account") or ""
        valid_accounts = {v for v, _ in Payment.Account.choices}
        if account and account not in valid_accounts:
            self.add_error("cash_account", "Choose a valid account.")

        total = cash_amount + cheque_total
        if total <= 0:
            raise forms.ValidationError("Nothing to settle.")

        cleaned["_cash_amount"] = cash_amount
        cleaned["_cheque_total"] = cheque_total
        cleaned["_total_paid"] = total
        return cleaned

    def _read_one_cheque(self, row, index):
        """Validate one cheque dict and return it cleaned up."""
        def _s(key):
            return str(row.get(key) or "").strip()

        cheque_no = _s("cheque_no")
        bank_name = _s("bank_name")
        if not cheque_no:
            raise forms.ValidationError(f"Cheque {index}: number is required.")
        if not bank_name:
            raise forms.ValidationError(f"Cheque {index}: bank name is required.")

        try:
            amount = Decimal(str(row.get("amount") or "").strip())
        except (InvalidOperation, TypeError):
            raise forms.ValidationError(f"Cheque {index}: amount must be a number.")
        if amount <= 0:
            raise forms.ValidationError(f"Cheque {index}: amount must be above 0.")

        received = parse_date(_s("received_date"))
        maturity = parse_date(_s("maturity_date"))
        if received is None:
            raise forms.ValidationError(f"Cheque {index}: received date is required.")
        if maturity is None:
            raise forms.ValidationError(f"Cheque {index}: maturity date is required.")
        if maturity < received:
            raise forms.ValidationError(
                f"Cheque {index}: maturity date cannot be before the received date."
            )

        return {
            "cheque_no": cheque_no,
            "bank_name": bank_name,
            "branch": _s("branch"),
            "acc_no": _s("acc_no"),
            "amount": amount.quantize(Decimal("0.01")),
            "received_date": received,
            "maturity_date": maturity,
        }

    def first_error(self):
        for messages in self.errors.values():
            return messages[0] if messages else "Could not save."
        return "Could not save."


class PettyCashExpenseForm(forms.ModelForm):
    """One expense out of the tin.

    `fund` and `added_by` are set by the view — the caller already knows the
    month and who is logged in — so this only asks for the operator inputs.
    entry_type is fixed to EXPENSE by the view path; reimbursements have
    their own model, not this one.
    """

    class Meta:
        model = PettyCashEntry
        fields = ["date", "description", "category", "amount", "receipt_no", "edit_reason"]
        labels = {
            "date": "Date",
            "description": "Description",
            "category": "Category",
            "amount": "Amount",
            "receipt_no": "Receipt no",
            "edit_reason": "Reason for edit",
        }
        widgets = {
            "date": forms.DateInput(
                format="%Y-%m-%d",
                attrs={"class": INPUT_CLASSES, "type": "date"},
            ),
            "description": forms.TextInput(
                attrs={"class": INPUT_CLASSES, "placeholder": "e.g. Fuel for delivery run"}
            ),
            "category": forms.Select(attrs={"class": SELECT_CLASSES}),
            "amount": forms.NumberInput(
                attrs={"class": INPUT_CLASSES, "step": "0.01", "min": "0.01", "placeholder": "0.00"}
            ),
            "receipt_no": forms.TextInput(
                attrs={"class": INPUT_CLASSES, "placeholder": "optional"}
            ),
            "edit_reason": forms.TextInput(
                attrs={"class": INPUT_CLASSES, "placeholder": "Why this correction?", "maxlength": 500}
            ),
        }

    def __init__(self, *args, require_edit_reason=False, **kwargs):
        super().__init__(*args, **kwargs)
        if not require_edit_reason:
            # Only shown on the edit modal — on create the field would just
            # sit blank and confuse the operator.
            self.fields.pop("edit_reason", None)
        if not self.initial.get("date") and not self.instance.pk:
            self.initial["date"] = timezone.localdate()
        if not self.initial.get("category") and not self.instance.pk:
            self.initial["category"] = PettyCashEntry.Category.OTHER

    def clean_amount(self):
        amount = self.cleaned_data["amount"]
        if amount <= 0:
            raise forms.ValidationError("Amount must be above 0.")
        return amount

    def clean_description(self):
        description = (self.cleaned_data.get("description") or "").strip()
        if not description:
            raise forms.ValidationError("Description is required.")
        return description

    def clean_edit_reason(self):
        reason = (self.cleaned_data.get("edit_reason") or "").strip()
        if "edit_reason" in self.fields and not reason:
            raise forms.ValidationError("Give a reason for this edit.")
        return reason

    def first_error(self):
        for messages in self.errors.values():
            return messages[0] if messages else "Could not save."
        return "Could not save."


class PettyCashReimbursementForm(forms.ModelForm):
    """One top-up of the tin. `fund` and `added_by` are set by the view."""

    class Meta:
        model = PettyCashReimbursement
        fields = ["date", "amount", "reason", "given_by", "edit_reason"]
        labels = {
            "date": "Date",
            "amount": "Amount",
            "reason": "Reason",
            "given_by": "Given by",
            "edit_reason": "Reason for edit",
        }
        widgets = {
            "date": forms.DateInput(
                format="%Y-%m-%d",
                attrs={"class": INPUT_CLASSES, "type": "date"},
            ),
            "amount": forms.NumberInput(
                attrs={"class": INPUT_CLASSES, "step": "0.01", "min": "0.01", "placeholder": "0.00"}
            ),
            "reason": forms.TextInput(
                attrs={"class": INPUT_CLASSES, "placeholder": "e.g. Weekly top-up from office cash"}
            ),
            "given_by": forms.TextInput(
                attrs={"class": INPUT_CLASSES, "placeholder": "Who handed over the cash"}
            ),
            "edit_reason": forms.TextInput(
                attrs={"class": INPUT_CLASSES, "placeholder": "Why this correction?", "maxlength": 500}
            ),
        }

    def __init__(self, *args, require_edit_reason=False, **kwargs):
        super().__init__(*args, **kwargs)
        if not require_edit_reason:
            self.fields.pop("edit_reason", None)
        if not self.initial.get("date") and not self.instance.pk:
            self.initial["date"] = timezone.localdate()

    def clean_amount(self):
        amount = self.cleaned_data["amount"]
        if amount <= 0:
            raise forms.ValidationError("Amount must be above 0.")
        return amount

    def clean_reason(self):
        reason = (self.cleaned_data.get("reason") or "").strip()
        if not reason:
            raise forms.ValidationError("Reason is required.")
        return reason

    def clean_given_by(self):
        given_by = (self.cleaned_data.get("given_by") or "").strip()
        if not given_by:
            raise forms.ValidationError("Say who gave the cash.")
        return given_by

    def clean_edit_reason(self):
        reason = (self.cleaned_data.get("edit_reason") or "").strip()
        if "edit_reason" in self.fields and not reason:
            raise forms.ValidationError("Give a reason for this edit.")
        return reason

    def first_error(self):
        for messages in self.errors.values():
            return messages[0] if messages else "Could not save."
        return "Could not save."


class MaterialSupplierForm(forms.ModelForm):
    class Meta:
        model = MaterialSupplier
        fields = ["name", "phone", "address", "is_active"]
        widgets = {
            "name": forms.TextInput(attrs={"class": INPUT_CLASSES, "placeholder": "e.g. Sunil Resin Traders"}),
            "phone": forms.TextInput(attrs={"class": INPUT_CLASSES, "placeholder": "e.g. 077 123 4567"}),
            "address": forms.Textarea(attrs={"class": INPUT_CLASSES, "rows": 2}),
            "is_active": forms.CheckboxInput(attrs={"class": CHECKBOX_CLASSES}),
        }

    def clean_name(self):
        name = (self.cleaned_data.get("name") or "").strip()
        if not name:
            raise forms.ValidationError("Name is required.")
        return name

    def first_error(self):
        for messages in self.errors.values():
            return messages[0] if messages else "Could not save."
        return "Could not save."


class MaterialForm(forms.ModelForm):
    class Meta:
        model = Material
        fields = ["name", "unit", "default_unit_price", "is_active"]
        widgets = {
            "name": forms.TextInput(attrs={"class": INPUT_CLASSES, "placeholder": "e.g. HDPE resin"}),
            "unit": forms.Select(attrs={"class": SELECT_CLASSES}),
            "default_unit_price": forms.NumberInput(
                attrs={"class": INPUT_CLASSES, "step": "0.01", "min": "0", "placeholder": "0.00"}
            ),
            "is_active": forms.CheckboxInput(attrs={"class": CHECKBOX_CLASSES}),
        }

    def clean_name(self):
        name = (self.cleaned_data.get("name") or "").strip()
        if not name:
            raise forms.ValidationError("Name is required.")
        return name

    def clean_default_unit_price(self):
        price = self.cleaned_data.get("default_unit_price") or Decimal("0")
        if price < 0:
            raise forms.ValidationError("Price cannot be negative.")
        return price

    def first_error(self):
        for messages in self.errors.values():
            return messages[0] if messages else "Could not save."
        return "Could not save."


class MaterialPurchaseHeaderForm(forms.ModelForm):
    """Just the header fields of a purchase — items ride in as JSON in the
    same POST and are handled by the view. `created_by` is set by the view."""

    class Meta:
        model = MaterialPurchase
        fields = ["supplier", "purchase_date", "invoice_no", "notes", "edit_reason"]
        widgets = {
            "supplier": forms.Select(attrs={"class": SELECT_CLASSES}),
            "purchase_date": forms.DateInput(
                format="%Y-%m-%d",
                attrs={"class": INPUT_CLASSES, "type": "date"},
            ),
            "invoice_no": forms.TextInput(
                attrs={"class": INPUT_CLASSES, "placeholder": "Supplier's invoice reference (optional)"}
            ),
            "notes": forms.Textarea(attrs={"class": INPUT_CLASSES, "rows": 2}),
            "edit_reason": forms.TextInput(
                attrs={"class": INPUT_CLASSES, "placeholder": "Why this correction?", "maxlength": 500}
            ),
        }

    def __init__(self, *args, require_edit_reason=False, **kwargs):
        super().__init__(*args, **kwargs)
        if not require_edit_reason:
            self.fields.pop("edit_reason", None)
        # Active suppliers only, unless editing a purchase whose supplier has
        # since been deactivated — the operator should still be able to save
        # corrections without losing the row's own supplier.
        active = MaterialSupplier.objects.filter(is_active=True)
        if self.instance and self.instance.pk and self.instance.supplier_id:
            active = MaterialSupplier.objects.filter(
                Q(is_active=True) | Q(pk=self.instance.supplier_id)
            )
        self.fields["supplier"].queryset = active
        if not self.initial.get("purchase_date") and not self.instance.pk:
            self.initial["purchase_date"] = timezone.localdate()

    def clean_purchase_date(self):
        purchase_date = self.cleaned_data["purchase_date"]
        if purchase_date > timezone.localdate():
            raise forms.ValidationError("Purchase can't be dated in the future.")
        return purchase_date

    def clean_edit_reason(self):
        reason = (self.cleaned_data.get("edit_reason") or "").strip()
        if "edit_reason" in self.fields and not reason:
            raise forms.ValidationError("Give a reason for this edit.")
        return reason

    def first_error(self):
        for messages in self.errors.values():
            return messages[0] if messages else "Could not save."
        return "Could not save."


class MaterialWeighEntryForm(forms.ModelForm):
    """One trip to the scale. `purchase_item` and `submitted_by` are set by
    the view."""

    class Meta:
        model = MaterialWeighEntry
        fields = ["weigh_date", "weighed_qty", "checked_by"]
        widgets = {
            "weigh_date": forms.DateInput(
                format="%Y-%m-%d",
                attrs={"class": INPUT_CLASSES, "type": "date"},
            ),
            "weighed_qty": forms.NumberInput(
                attrs={"class": INPUT_CLASSES, "step": "0.001", "min": "0.001", "placeholder": "0.000"}
            ),
            "checked_by": forms.TextInput(
                attrs={"class": INPUT_CLASSES, "placeholder": "Store hand at the scale"}
            ),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if not self.initial.get("weigh_date") and not self.instance.pk:
            self.initial["weigh_date"] = timezone.localdate()

    def clean_weighed_qty(self):
        qty = self.cleaned_data.get("weighed_qty") or Decimal("0")
        if qty <= 0:
            raise forms.ValidationError("Weighed quantity must be above 0.")
        return qty

    def clean_checked_by(self):
        checked_by = (self.cleaned_data.get("checked_by") or "").strip()
        if not checked_by:
            raise forms.ValidationError("Say who checked the weight.")
        return checked_by

    def first_error(self):
        for messages in self.errors.values():
            return messages[0] if messages else "Could not save."
        return "Could not save."


class VehicleForm(forms.ModelForm):
    class Meta:
        model = Vehicle
        fields = ["name", "registration_no", "is_active"]
        widgets = {
            "name": forms.TextInput(attrs={"class": INPUT_CLASSES, "placeholder": "e.g. Lorry — WP ABC 1234"}),
            "registration_no": forms.TextInput(attrs={"class": INPUT_CLASSES, "placeholder": "e.g. WP ABC 1234"}),
            "is_active": forms.CheckboxInput(attrs={"class": CHECKBOX_CLASSES}),
        }

    def clean_name(self):
        name = (self.cleaned_data.get("name") or "").strip()
        if not name:
            raise forms.ValidationError("Name is required.")
        return name

    def first_error(self):
        for messages in self.errors.values():
            return messages[0] if messages else "Could not save."
        return "Could not save."


class RiderForm(forms.ModelForm):
    class Meta:
        model = Rider
        fields = ["name", "phone", "is_active"]
        widgets = {
            "name": forms.TextInput(attrs={"class": INPUT_CLASSES, "placeholder": "e.g. Nimal"}),
            "phone": forms.TextInput(attrs={"class": INPUT_CLASSES, "placeholder": "e.g. 077 123 4567"}),
            "is_active": forms.CheckboxInput(attrs={"class": CHECKBOX_CLASSES}),
        }

    def clean_name(self):
        name = (self.cleaned_data.get("name") or "").strip()
        if not name:
            raise forms.ValidationError("Name is required.")
        return name

    def first_error(self):
        for messages in self.errors.values():
            return messages[0] if messages else "Could not save."
        return "Could not save."


class VehicleTripForm(forms.ModelForm):
    """One trip. `added_by` is set by the view.

    The vehicle and rider dropdowns default to active only. On edit, whichever
    vehicle or rider the row already points at is kept in the queryset even
    if it has since been deactivated — the operator should be able to correct
    an old trip without losing its own references.
    """

    class Meta:
        model = VehicleTrip
        fields = ["trip_date", "vehicle", "rider", "from_location", "to_location", "km", "purpose"]
        widgets = {
            "trip_date": forms.DateInput(
                format="%Y-%m-%d",
                attrs={"class": INPUT_CLASSES, "type": "date"},
            ),
            "vehicle": forms.Select(attrs={"class": SELECT_CLASSES}),
            "rider": forms.Select(attrs={"class": SELECT_CLASSES}),
            "from_location": forms.TextInput(attrs={"class": INPUT_CLASSES, "placeholder": "e.g. Yard"}),
            "to_location": forms.TextInput(attrs={"class": INPUT_CLASSES, "placeholder": "e.g. Kandy"}),
            "km": forms.NumberInput(attrs={"class": INPUT_CLASSES, "step": "0.01", "min": "0.01", "placeholder": "0.00"}),
            "purpose": forms.TextInput(attrs={"class": INPUT_CLASSES, "placeholder": "e.g. Delivery to customer"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if not self.initial.get("trip_date") and not self.instance.pk:
            self.initial["trip_date"] = timezone.localdate()

        vehicles = Vehicle.objects.filter(is_active=True)
        riders = Rider.objects.filter(is_active=True)
        if self.instance and self.instance.pk:
            if self.instance.vehicle_id:
                vehicles = Vehicle.objects.filter(
                    Q(is_active=True) | Q(pk=self.instance.vehicle_id)
                )
            if self.instance.rider_id:
                riders = Rider.objects.filter(
                    Q(is_active=True) | Q(pk=self.instance.rider_id)
                )
        self.fields["vehicle"].queryset = vehicles
        self.fields["rider"].queryset = riders

    def clean_km(self):
        km = self.cleaned_data.get("km") or Decimal("0")
        if km <= 0:
            raise forms.ValidationError("KM must be above 0.")
        return km

    def clean_from_location(self):
        return (self.cleaned_data.get("from_location") or "").strip()

    def clean_to_location(self):
        return (self.cleaned_data.get("to_location") or "").strip()

    def clean_trip_date(self):
        trip_date = self.cleaned_data["trip_date"]
        if trip_date > timezone.localdate():
            raise forms.ValidationError("A trip can't be dated in the future.")
        return trip_date

    def first_error(self):
        for messages in self.errors.values():
            return messages[0] if messages else "Could not save."
        return "Could not save."


class OrderHeaderForm(forms.ModelForm):
    """Just the header fields of a quotation — items ride in as JSON and are
    validated by the view. `created_by` is set by the view.

    A quotation may be addressed to either an existing Customer FK or a
    free-text `customer_name` (walk-in). The view enforces exactly one of
    those; this form does not, because the two are set from separate UI
    controls that the JS keeps in step.
    """

    class Meta:
        model = Order
        fields = [
            "customer", "customer_name",
            "order_date", "valid_until",
            "notes",
            "delivery_charge", "discount_amount", "discount_reason",
            "edit_reason",
        ]
        widgets = {
            "customer": forms.Select(attrs={"class": SELECT_CLASSES}),
            "customer_name": forms.TextInput(
                attrs={"class": INPUT_CLASSES, "placeholder": "Walk-in name"}
            ),
            "order_date": forms.DateInput(
                format="%Y-%m-%d",
                attrs={"class": INPUT_CLASSES, "type": "date"},
            ),
            "valid_until": forms.DateInput(
                format="%Y-%m-%d",
                attrs={"class": INPUT_CLASSES, "type": "date"},
            ),
            "notes": forms.Textarea(attrs={"class": INPUT_CLASSES, "rows": 2}),
            "delivery_charge": forms.NumberInput(
                attrs={"class": INPUT_CLASSES, "step": "0.01", "min": "0", "placeholder": "0.00"}
            ),
            "discount_amount": forms.NumberInput(
                attrs={"class": INPUT_CLASSES, "step": "0.01", "min": "0", "placeholder": "0.00"}
            ),
            "discount_reason": forms.TextInput(
                attrs={"class": INPUT_CLASSES, "maxlength": 255, "placeholder": "Why?"}
            ),
            "edit_reason": forms.TextInput(
                attrs={"class": INPUT_CLASSES, "placeholder": "Why this correction?", "maxlength": 500}
            ),
        }

    def __init__(self, *args, require_edit_reason=False, **kwargs):
        super().__init__(*args, **kwargs)
        if not require_edit_reason:
            self.fields.pop("edit_reason", None)
        # Any active party may be quoted — including suppliers (who may buy
        # too) — mirroring _billable_customers.
        active = Customer.objects.filter(is_active=True, is_walk_in_account=False)
        if self.instance and self.instance.pk and self.instance.customer_id:
            active = Customer.objects.filter(
                Q(is_active=True, is_walk_in_account=False)
                | Q(pk=self.instance.customer_id)
            )
        self.fields["customer"].queryset = active
        self.fields["customer"].required = False
        if not self.initial.get("order_date") and not self.instance.pk:
            self.initial["order_date"] = timezone.localdate()

    def clean_delivery_charge(self):
        value = self.cleaned_data.get("delivery_charge") or Decimal("0")
        if value < 0:
            raise forms.ValidationError("Delivery charge cannot be negative.")
        return value

    def clean_discount_amount(self):
        value = self.cleaned_data.get("discount_amount") or Decimal("0")
        if value < 0:
            raise forms.ValidationError("Discount cannot be negative.")
        return value

    def clean_edit_reason(self):
        reason = (self.cleaned_data.get("edit_reason") or "").strip()
        if "edit_reason" in self.fields and not reason:
            raise forms.ValidationError("Give a reason for this edit.")
        return reason

    def clean(self):
        cleaned = super().clean()
        customer = cleaned.get("customer")
        walk_in_name = (cleaned.get("customer_name") or "").strip()

        # Exactly one of them must be set. Two would be ambiguous (who is
        # this quotation actually for?), zero would leave the PDF without an
        # addressee.
        if customer and walk_in_name:
            raise forms.ValidationError(
                "Pick either an existing customer or enter a walk-in name — not both."
            )
        if not customer and not walk_in_name:
            raise forms.ValidationError(
                "Pick a customer or enter a walk-in name."
            )

        if cleaned.get("discount_amount") and cleaned.get("discount_amount") > 0:
            if not (cleaned.get("discount_reason") or "").strip():
                self.add_error(
                    "discount_reason", "Give a reason for the discount."
                )

        return cleaned

    def first_error(self):
        for messages in self.errors.values():
            return messages[0] if messages else "Could not save."
        return "Could not save."


class StockAdjustmentForm(forms.ModelForm):
    """One manual stock correction for a product.

    `qty` is signed: positive adds to the shelf, negative removes. `product`
    and `adjusted_by` are set by the view.
    """

    class Meta:
        model = StockAdjustment
        fields = ["adjustment_date", "qty", "reason"]
        labels = {
            "adjustment_date": "Adjustment date",
            "qty": "Adjustment (signed)",
            "reason": "Reason",
        }
        widgets = {
            "adjustment_date": forms.DateInput(
                format="%Y-%m-%d",
                attrs={"class": INPUT_CLASSES, "type": "date"},
            ),
            "qty": forms.NumberInput(
                attrs={
                    "class": INPUT_CLASSES,
                    "step": "0.001",
                    "placeholder": "+ to add, − to remove (e.g. 100 or -20)",
                }
            ),
            "reason": forms.TextInput(
                attrs={
                    "class": INPUT_CLASSES,
                    "placeholder": "e.g. Counted shelf 100 short, or scrap 20 damaged",
                    "maxlength": 500,
                }
            ),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if not self.initial.get("adjustment_date") and not self.instance.pk:
            self.initial["adjustment_date"] = timezone.localdate()

    def clean_qty(self):
        qty = self.cleaned_data.get("qty")
        if qty is None:
            raise forms.ValidationError("Enter a quantity.")
        if qty == 0:
            raise forms.ValidationError(
                "Adjustment cannot be zero — nothing would change."
            )
        return qty

    def clean_reason(self):
        reason = (self.cleaned_data.get("reason") or "").strip()
        if not reason:
            raise forms.ValidationError("Give a reason for this adjustment.")
        return reason

    def clean_adjustment_date(self):
        d = self.cleaned_data["adjustment_date"]
        if d > timezone.localdate():
            raise forms.ValidationError("Adjustment can't be dated in the future.")
        return d

    def first_error(self):
        for messages in self.errors.values():
            return messages[0] if messages else "Could not save."
        return "Could not save."


class MachineForm(forms.ModelForm):
    class Meta:
        model = Machine
        fields = ["name", "is_active", "notes"]
        widgets = {
            "name": forms.TextInput(attrs={"class": INPUT_CLASSES, "placeholder": "e.g. Blowing Machine 1"}),
            "notes": forms.Textarea(attrs={"class": INPUT_CLASSES, "rows": 2}),
            "is_active": forms.CheckboxInput(attrs={"class": CHECKBOX_CLASSES}),
        }

    def clean_name(self):
        name = (self.cleaned_data.get("name") or "").strip()
        if not name:
            raise forms.ValidationError("Name is required.")
        return name

    def first_error(self):
        for messages in self.errors.values():
            return messages[0] if messages else "Could not save."
        return "Could not save."


class DailyOtherWorkForm(forms.ModelForm):
    class Meta:
        model = DailyOtherWork
        fields = ["driver", "material_supply", "material_mixing", "other"]
        widgets = {
            "driver": forms.TextInput(
                attrs={"class": INPUT_CLASSES, "placeholder": "Who drove today, where to"}
            ),
            "material_supply": forms.TextInput(
                attrs={"class": INPUT_CLASSES, "placeholder": "Who supplied material to which machine"}
            ),
            "material_mixing": forms.TextInput(
                attrs={"class": INPUT_CLASSES, "placeholder": "Who mixed which material"}
            ),
            "other": forms.Textarea(
                attrs={"class": INPUT_CLASSES, "rows": 3, "placeholder": "Anything else worth logging"}
            ),
        }

    def first_error(self):
        for messages in self.errors.values():
            return messages[0] if messages else "Could not save."
        return "Could not save."
