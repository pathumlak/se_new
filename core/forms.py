from decimal import Decimal

from django import forms
from django.utils import timezone

from .models import (
    CashDrawer,
    Category,
    Cheque,
    Customer,
    CustomerBalanceAdjustment,
    Payment,
    Product,
    ProductionEntry,
    User,
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

