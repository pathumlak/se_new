import json
from datetime import timedelta
from decimal import Decimal

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.db.models import (
    Case,
    Count,
    DecimalField,
    ExpressionWrapper,
    F,
    ProtectedError,
    Q,
    Sum,
    Value,
    When,
)
from django.db.models.functions import Coalesce
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.formats import date_format
from django.views.decorators.http import require_POST

from .decorators import super_admin_required
from .forms import CategoryForm, CustomerPriceForm, ProductForm
from .models import (
    Bill,
    CashDrawer,
    Category,
    Cheque,
    Customer,
    CustomerPrice,
    Product,
)

#: A cheque is "maturing soon" this many days out.
CHEQUE_WARNING_DAYS = 3

MONEY = DecimalField(max_digits=12, decimal_places=2)
ZERO = Decimal("0.00")


def _cash_drawer_balance():
    """Net cash on hand: 'in' adds, 'out' and 'transfer' both remove."""
    return CashDrawer.objects.aggregate(
        total=Coalesce(
            Sum(
                Case(
                    When(txn_type=CashDrawer.TxnType.IN, then=F("amount")),
                    default=-F("amount"),
                    output_field=MONEY,
                )
            ),
            ZERO,
            output_field=MONEY,
        )
    )["total"]


@login_required
def dashboard(request):
    today = timezone.localdate()
    horizon = today + timedelta(days=CHEQUE_WARNING_DAYS)

    # Debtors carry a negative balance, so the sum is negative; flip it so the
    # card reads as a positive amount owed to the company.
    owed = Customer.objects.filter(balance__lt=0).aggregate(
        total=Coalesce(Sum("balance"), ZERO, output_field=MONEY)
    )["total"]
    total_outstanding = -owed

    todays_sales = (
        Bill.objects.filter(bill_date=today)
        .exclude(status=Bill.Status.CANCELLED)
        .aggregate(total=Coalesce(Sum("total_amount"), ZERO, output_field=MONEY))["total"]
    )

    maturing_cheques = (
        Cheque.objects.filter(
            maturity_date__gte=today,
            maturity_date__lte=horizon,
            status__in=[Cheque.Status.PENDING, Cheque.Status.HELD],
        )
        .select_related("customer")
        .order_by("maturity_date")
    )

    recent_bills = Bill.objects.select_related("customer")[:5]

    # `owed` is the positive amount the customer owes, for display.
    top_customers = (
        Customer.objects.filter(balance__lt=0)
        .annotate(
            owed=ExpressionWrapper(Value(0) - F("balance"), output_field=MONEY)
        )
        .order_by("balance")[:5]
    )

    return render(
        request,
        "core/dashboard.html",
        {
            "total_outstanding": total_outstanding,
            "todays_sales": todays_sales,
            "cash_balance": _cash_drawer_balance(),
            "maturing_cheques": maturing_cheques,
            "maturing_count": maturing_cheques.count(),
            "recent_bills": recent_bills,
            "top_customers": top_customers,
            "warning_days": CHEQUE_WARNING_DAYS,
            "today": today,
        },
    )


# ---------------------------------------------------------------- categories
# Super-admin only. Managers are redirected to the dashboard with an error.


@super_admin_required
def category_list(request):
    query = request.GET.get("q", "").strip()

    categories = Category.objects.annotate(product_count=Count("products"))
    if query:
        categories = categories.filter(
            Q(name__icontains=query) | Q(description__icontains=query)
        )

    return render(
        request,
        "core/category_list.html",
        {
            "categories": categories,
            "query": query,
            "total_count": Category.objects.count(),
        },
    )


@super_admin_required
def category_create(request):
    form = CategoryForm(request.POST or None)

    if request.method == "POST" and form.is_valid():
        category = form.save()
        messages.success(request, f"Category '{category.name}' was created.")
        return redirect("core:category_list")

    return render(
        request,
        "core/category_form.html",
        {"form": form, "is_edit": False},
    )


@super_admin_required
def category_update(request, pk):
    category = get_object_or_404(Category, pk=pk)
    form = CategoryForm(request.POST or None, instance=category)

    if request.method == "POST" and form.is_valid():
        category = form.save()
        messages.success(request, f"Category '{category.name}' was updated.")
        return redirect("core:category_list")

    return render(
        request,
        "core/category_form.html",
        {"form": form, "category": category, "is_edit": True},
    )


@require_POST
@super_admin_required
def category_delete(request, pk):
    category = get_object_or_404(Category, pk=pk)
    name = category.name

    try:
        category.delete()
    except ProtectedError:
        # Product.category is on_delete=PROTECT, so a category still holding
        # products cannot be removed. Explain rather than 500.
        count = category.products.count()
        messages.error(
            request,
            f"Cannot delete '{name}' — {count} product{'' if count == 1 else 's'} "
            f"still belong to it. Reassign or remove them first.",
        )
    else:
        messages.success(request, f"Category '{name}' was deleted.")

    return redirect("core:category_list")


# ------------------------------------------------------------------ products
# Managers may list, create and edit. Deleting is super-admin only.


@login_required
def product_list(request):
    query = request.GET.get("q", "").strip()
    category_id = request.GET.get("category", "").strip()

    products = Product.objects.select_related("category").annotate(
        custom_price_count=Count("customer_prices")
    )
    if query:
        products = products.filter(name__icontains=query)

    # An unparsable ?category= is ignored rather than 500ing on a bad filter.
    selected_category = None
    if category_id.isdigit():
        selected_category = int(category_id)
        products = products.filter(category_id=selected_category)

    return render(
        request,
        "core/product_list.html",
        {
            "products": products,
            "categories": Category.objects.all(),
            "query": query,
            "selected_category": selected_category,
            "is_filtered": bool(query or selected_category),
            "total_count": Product.objects.count(),
        },
    )


@login_required
def product_create(request):
    form = ProductForm(request.POST or None)

    if request.method == "POST" and form.is_valid():
        product = form.save()
        messages.success(request, f"Product '{product}' was created.")
        return redirect("core:product_list")

    return render(
        request,
        "core/product_form.html",
        {"form": form, "is_edit": False},
    )


@login_required
def product_update(request, pk):
    product = get_object_or_404(Product, pk=pk)
    form = ProductForm(request.POST or None, instance=product)

    if request.method == "POST" and form.is_valid():
        product = form.save()
        messages.success(request, f"Product '{product}' was updated.")
        return redirect("core:product_list")

    return render(
        request,
        "core/product_form.html",
        {"form": form, "product": product, "is_edit": True},
    )


@require_POST
@super_admin_required
def product_delete(request, pk):
    product = get_object_or_404(Product, pk=pk)
    label = str(product)

    try:
        product.delete()
    except ProtectedError:
        # Bill items, supplier bill items and production entries all PROTECT
        # their product, so anything with history stays put.
        messages.error(
            request,
            f"Cannot delete '{label}' — it is used by bills, supplier bills or "
            f"production entries. Deactivate it instead to hide it from new bills.",
        )
    else:
        messages.success(request, f"Product '{label}' was deleted.")

    return redirect("core:product_list")


@require_POST
@login_required
def product_toggle_active(request, pk):
    """Flip is_active from the list page. Answers JSON for the inline toggle."""
    product = get_object_or_404(Product, pk=pk)
    product.is_active = not product.is_active
    product.save(update_fields=["is_active"])

    return JsonResponse(
        {
            "ok": True,
            "is_active": product.is_active,
            "label": "Active" if product.is_active else "Inactive",
        }
    )


# ----------------------------------------------------------- customer prices
# The same CustomerPrice grid seen from either side: all customers for one
# product, or all products for one customer. Both save through the endpoint
# below, so the two pages can never disagree about what a save does.

#: Timestamp format shared by the first paint and the AJAX reply — a saved row
#: must not drift out of step with the untouched rows around it.
UPDATED_FORMAT = "M j, Y g:i a"

#: Ceiling on one Save All. Comfortably above a full product list, but stops a
#: hand-rolled payload from turning into an unbounded write.
MAX_BULK_ROWS = 500

MALFORMED = "Malformed request — reload the page and try again."


def _format_updated(dt):
    return date_format(timezone.localtime(dt), UPDATED_FORMAT)


@login_required
def product_prices(request, pk):
    """Every customer's negotiated price for one product."""
    product = get_object_or_404(Product.objects.select_related("category"), pk=pk)

    existing = {
        cp.customer_id: cp for cp in CustomerPrice.objects.filter(product=product)
    }

    # Suppliers aren't sold to, so they stay off the list — unless this product
    # is already priced for one, since hiding the row would hide the data.
    customers = Customer.objects.filter(
        Q(is_active=True, is_supplier=False) | Q(pk__in=list(existing))
    )

    rows = []
    for customer in customers:
        price = existing.get(customer.pk)
        rows.append(
            {
                "customer": customer,
                "has_custom": price is not None,
                "unit_price": price.unit_price if price else None,
                "updated_at": _format_updated(price.updated_at) if price else "",
            }
        )

    return render(
        request,
        "core/product_prices.html",
        {
            "product": product,
            "rows": rows,
            "custom_count": len(existing),
        },
    )


@login_required
def customer_prices(request, pk):
    """Every product's negotiated price for one customer."""
    customer = get_object_or_404(Customer, pk=pk)

    existing = {
        cp.product_id: cp for cp in CustomerPrice.objects.filter(customer=customer)
    }

    # Same reasoning as above: a retired product that still carries a custom
    # price for this customer stays visible.
    products = Product.objects.select_related("category").filter(
        Q(is_active=True) | Q(pk__in=list(existing))
    )

    rows = []
    for product in products:
        price = existing.get(product.pk)
        rows.append(
            {
                "product": product,
                "has_custom": price is not None,
                "unit_price": price.unit_price if price else None,
                "updated_at": _format_updated(price.updated_at) if price else "",
            }
        )

    return render(
        request,
        "core/customer_prices.html",
        {
            "customer": customer,
            "rows": rows,
            "custom_count": len(existing),
        },
    )


@require_POST
@login_required
def customer_price_save_all(request):
    """Save every edited row of a price table in one request.

    Takes JSON: {"rows": [{"customer_id", "product_id", "unit_price"}, ...]}.
    The whole batch is validated before anything is written, and the write is
    wrapped in one transaction: a single bad row saves nothing. Half-applying
    a batch would leave the operator guessing which half landed.
    """
    try:
        payload = json.loads(request.body or b"{}")
    except json.JSONDecodeError:
        return JsonResponse({"success": False, "error": MALFORMED}, status=400)

    rows = payload.get("rows") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        return JsonResponse({"success": False, "error": MALFORMED}, status=400)
    if not rows:
        return JsonResponse({"success": False, "error": "No changes to save."}, status=400)
    if len(rows) > MAX_BULK_ROWS:
        return JsonResponse(
            {
                "success": False,
                "error": f"Too many rows in one save (limit {MAX_BULK_ROWS}).",
            },
            status=400,
        )

    valid = []
    errors = []
    for row in rows:
        if not isinstance(row, dict):
            return JsonResponse({"success": False, "error": MALFORMED}, status=400)

        form = CustomerPriceForm(row)
        if form.is_valid():
            valid.append(form.cleaned_data)
        else:
            # Echo the ids back so the page can pin the message to its row.
            errors.append(
                {
                    "customer_id": str(row.get("customer_id", "")),
                    "product_id": str(row.get("product_id", "")),
                    "error": form.first_error(),
                }
            )

    if errors:
        count = len(errors)
        return JsonResponse(
            {
                "success": False,
                "error": f"Nothing saved — fix {count} row{'' if count == 1 else 's'} and try again.",
                "errors": errors,
            },
            status=400,
        )

    results = []
    with transaction.atomic():
        for data in valid:
            # update_or_create leans on the (customer, product) unique
            # constraint, so two operators saving the same row race to an
            # update rather than to a duplicate row or an IntegrityError.
            price, created = CustomerPrice.objects.update_or_create(
                customer=data["customer_id"],
                product=data["product_id"],
                defaults={"unit_price": data["unit_price"]},
            )
            results.append(
                {
                    "customer_id": str(price.customer_id),
                    "product_id": str(price.product_id),
                    "created": created,
                    "price": f"{price.unit_price:.2f}",
                    "updated_at": _format_updated(price.updated_at),
                }
            )

    return JsonResponse({"success": True, "saved": len(results), "results": results})


@login_required
def customer_list(request):
    return render(request, "core/placeholder.html", {"section": "Customers"})


@login_required
def make_bill(request):
    return render(request, "core/placeholder.html", {"section": "Make Bill"})


@login_required
def bill_list(request):
    return render(request, "core/placeholder.html", {"section": "Bill List"})


@login_required
def cheque_list(request):
    return render(request, "core/placeholder.html", {"section": "Cheques"})


@login_required
def cash_drawer(request):
    return render(request, "core/placeholder.html", {"section": "Cash Drawer"})


@login_required
def supplier_bill_list(request):
    return render(request, "core/placeholder.html", {"section": "Supplier Bills"})


@login_required
def production(request):
    return render(request, "core/placeholder.html", {"section": "Production"})


@login_required
def customer_ledger(request):
    return render(request, "core/placeholder.html", {"section": "Customer Ledger"})


@login_required
def sales_report(request):
    return render(request, "core/placeholder.html", {"section": "Sales Report"})
