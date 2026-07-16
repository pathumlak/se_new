from decimal import Decimal

from django import forms

from .models import Category, Customer, Product

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
