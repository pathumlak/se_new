from datetime import timedelta
from decimal import Decimal

from django.contrib import messages
from django.contrib.auth.decorators import login_required
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
from django.views.decorators.http import require_POST

from .decorators import super_admin_required
from .forms import CategoryForm, ProductForm
from .models import Bill, CashDrawer, Category, Cheque, Customer, Product

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
