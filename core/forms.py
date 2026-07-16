from decimal import Decimal

from django import forms

from .models import CashDrawer, Category, Cheque, Customer, Product, ProductionEntry

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

    `balance` is deliberately absent: it moves only through bills, payments and
    cheques, so it must never be typed in here.

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
            "credit_limit",
            "is_supplier",
            "is_active",
        ]
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

    Only the quantity: the product and the date say what was made and when,
    and changing those makes it a different entry. stock_before and stock_after
    are snapshots the view maintains, never typed.
    """

    class Meta:
        model = ProductionEntry
        fields = ["qty_produced"]
        labels = {"qty_produced": "Quantity produced"}
        widgets = {
            "qty_produced": forms.NumberInput(
                attrs={
                    "class": INPUT_CLASSES,
                    "step": "0.001",
                    "min": "0",
                    "autofocus": True,
                }
            ),
        }

    def clean_qty_produced(self):
        qty = self.cleaned_data["qty_produced"]
        if qty <= 0:
            raise forms.ValidationError(
                "Quantity must be above 0. Delete the entry instead."
            )
        return qty


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
