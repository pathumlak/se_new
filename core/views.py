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
from django.db.models.functions import Coalesce, Greatest
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.dateparse import parse_date
from django.utils.formats import date_format
from django.views.decorators.http import require_GET, require_POST

from .decorators import super_admin_required
from .forms import CategoryForm, CustomerForm, CustomerPriceForm, ProductForm
from .models import (
    Bill,
    CashDrawer,
    Category,
    Cheque,
    Customer,
    CustomerPrice,
    Payment,
    Product,
    SupplierBill,
    User,
)

#: A cheque is "maturing soon" this many days out.
CHEQUE_WARNING_DAYS = 3

MONEY = DecimalField(max_digits=12, decimal_places=2)
ZERO = Decimal("0.00")


def _is_super_admin(user):
    """Mirrors the check in `super_admin_required`, for views that stay open to
    managers but hand them a reduced form."""
    return getattr(user, "role", None) == User.Role.SUPER_ADMIN


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


# ---------------------------------------------------------------- customers
# Managers may list, view, create and edit. Deleting is super-admin only, and
# so is the credit limit — see CustomerForm.


def _customers():
    """Customers annotated with everything the list and detail pages report.

    `owed` is the positive amount a debtor owes us: balances run negative when
    the customer owes, positive when we owe them. Available credit is the limit
    less what they already owe, floored at zero — someone past their limit has
    none left, never a negative amount.
    """
    owed = Case(
        When(balance__lt=0, then=Value(0) - F("balance")),
        default=Value(ZERO),
        output_field=MONEY,
    )
    return (
        Customer.objects.annotate(owed=owed)
        .annotate(
            available_credit=Greatest(
                F("credit_limit") - F("owed"), Value(ZERO), output_field=MONEY
            )
        )
        # distinct=True: without it these four joins multiply each other's rows
        # and every count comes out inflated.
        .annotate(
            bill_count=Count("bills", distinct=True),
            supplier_bill_count=Count("supplier_bills", distinct=True),
            cheque_count=Count("cheques", distinct=True),
            custom_price_count=Count("custom_prices", distinct=True),
        )
        .annotate(
            history_count=F("bill_count") + F("supplier_bill_count") + F("cheque_count")
        )
    )


def _delete_blockers(customer):
    """History that PROTECTs this customer, as printable phrases.

    Takes a customer from `_customers()` — the counts are already annotated.
    """
    counts = [
        (customer.bill_count, "bill"),
        (customer.supplier_bill_count, "supplier bill"),
        (customer.cheque_count, "cheque"),
    ]
    phrases = [f"{n} {label}{'' if n == 1 else 's'}" for n, label in counts if n]

    if len(phrases) <= 1:
        return "".join(phrases)
    return f"{', '.join(phrases[:-1])} and {phrases[-1]}"


@login_required
def customer_list(request):
    query = request.GET.get("q", "").strip()

    # Unrecognised filter values are dropped rather than 500ing or silently
    # showing an unfiltered list that claims to be filtered.
    kind = request.GET.get("kind", "").strip()
    if kind not in {"customers", "suppliers"}:
        kind = ""
    status = request.GET.get("status", "").strip()
    if status not in {"active", "inactive"}:
        status = ""

    customers = _customers()
    if query:
        customers = customers.filter(name__icontains=query)
    if kind:
        customers = customers.filter(is_supplier=kind == "suppliers")
    if status:
        customers = customers.filter(is_active=status == "active")

    return render(
        request,
        "core/customer_list.html",
        {
            "customers": customers,
            "query": query,
            "kind": kind,
            "status": status,
            "is_filtered": bool(query or kind or status),
            "total_count": Customer.objects.count(),
        },
    )


@login_required
def customer_detail(request, pk):
    customer = get_object_or_404(_customers(), pk=pk)
    return render(
        request,
        "core/customer_detail.html",
        {"customer": customer, "blockers": _delete_blockers(customer)},
    )


@login_required
def customer_create(request):
    form = CustomerForm(
        request.POST or None, is_super_admin=_is_super_admin(request.user)
    )

    if request.method == "POST" and form.is_valid():
        customer = form.save()
        messages.success(request, f"Customer '{customer.name}' was created.")
        return redirect("core:customer_list")

    return render(
        request,
        "core/customer_form.html",
        {"form": form, "is_edit": False},
    )


@login_required
def customer_update(request, pk):
    customer = get_object_or_404(Customer, pk=pk)
    form = CustomerForm(
        request.POST or None,
        instance=customer,
        is_super_admin=_is_super_admin(request.user),
    )

    if request.method == "POST" and form.is_valid():
        customer = form.save()
        messages.success(request, f"Customer '{customer.name}' was updated.")
        return redirect("core:customer_list")

    return render(
        request,
        "core/customer_form.html",
        {"form": form, "customer": customer, "is_edit": True},
    )


def _parse_date(raw):
    """A GET date, or None. An unparsable one is ignored rather than 500ing."""
    raw = (raw or "").strip()
    if not raw:
        return None
    try:
        return parse_date(raw)
    except ValueError:
        # Well-formed but impossible, e.g. 2026-02-31.
        return None


def _ledger_rows(customer, from_date=None, to_date=None):
    """Every ledger line for one customer, oldest first, with a running balance.

    Three sources land in one column layout:
      Sale      — a bill, what they now owe us            -> SALE
      Payment   — cash/cheque taken against a bill        -> CHE/CASH
      Purchase  — a supplier bill, what we owe them back  -> CHE/CASH

    The running balance is accumulated here rather than in the template: each
    row depends on every row before it, which a template cannot express
    without carrying state.

    Cancelled bills and their payments are left out — a cancelled sale is not
    owed, so including it would overstate the balance.
    """
    entries = []

    for bill in customer.bills.exclude(status=Bill.Status.CANCELLED):
        entries.append(
            {
                "date": bill.bill_date,
                "kind": 0,  # a sale precedes same-day money against it
                "pk": bill.pk,
                "description": "Sale",
                "sale": bill.total_amount,
                "credit": None,
            }
        )

    for supplier_bill in customer.supplier_bills.exclude(
        status=SupplierBill.Status.CANCELLED
    ):
        note = supplier_bill.notes.strip()
        entries.append(
            {
                "date": supplier_bill.bill_date,
                "kind": 1,
                "pk": supplier_bill.pk,
                "description": f"Purchase - {note}" if note else "Purchase",
                "sale": None,
                "credit": supplier_bill.total_amount,
            }
        )

    payments = (
        Payment.objects.filter(bill__customer=customer)
        .exclude(bill__status=Bill.Status.CANCELLED)
        .select_related("bill")
    )
    for payment in payments:
        entries.append(
            {
                # paid_at is a moment; the ledger reports days.
                "date": timezone.localdate(payment.paid_at),
                "kind": 2,
                "pk": payment.pk,
                "description": f"{payment.get_method_display()} received",
                "sale": None,
                "credit": payment.amount,
            }
        )

    if from_date:
        entries = [e for e in entries if e["date"] >= from_date]
    if to_date:
        entries = [e for e in entries if e["date"] <= to_date]

    # pk breaks the last tie, so two rows on one day never swap between loads.
    entries.sort(key=lambda e: (e["date"], e["kind"], e["pk"]))

    balance = ZERO
    for entry in entries:
        balance += (entry["sale"] or ZERO) - (entry["credit"] or ZERO)
        entry["balance"] = balance

    return entries


@login_required
def customer_ledger(request, pk):
    customer = get_object_or_404(_customers(), pk=pk)

    from_date = _parse_date(request.GET.get("from_date"))
    to_date = _parse_date(request.GET.get("to_date"))
    rows = _ledger_rows(customer, from_date, to_date)

    return render(
        request,
        "core/customer_ledger.html",
        {
            "customer": customer,
            "rows": rows,
            "from_date": from_date,
            "to_date": to_date,
            "is_filtered": bool(from_date or to_date),
            "total_sale": sum((r["sale"] or ZERO for r in rows), ZERO),
            "total_credit": sum((r["credit"] or ZERO for r in rows), ZERO),
            "closing_balance": rows[-1]["balance"] if rows else ZERO,
        },
    )


@require_POST
@super_admin_required
def customer_delete(request, pk):
    customer = get_object_or_404(_customers(), pk=pk)
    name = customer.name

    blockers = _delete_blockers(customer)
    if blockers:
        # Bills, supplier bills and cheques all PROTECT their customer, so
        # anything with trading history stays put. Say what is holding it
        # rather than letting the delete fail at the database.
        messages.error(
            request,
            f"Cannot delete '{name}' — it still has {blockers}. "
            f"Deactivate it instead to hide it from new bills.",
        )
        return redirect("core:customer_list")

    try:
        customer.delete()
    except ProtectedError:
        # Belt and braces: a PROTECTed relation added later lands here rather
        # than as a 500.
        messages.error(
            request,
            f"Cannot delete '{name}' — other records still reference it. "
            f"Deactivate it instead.",
        )
    else:
        messages.success(request, f"Customer '{name}' was deleted.")

    return redirect("core:customer_list")


# -------------------------------------------------------------------- bills
# Bill creation is one page: picking a customer pulls their own prices over
# AJAX, so nothing reloads between steps.


def _qty_text(value):
    """Stock as the product list prints it: up to 3 dp, no trailing zeros."""
    text = f"{value:.3f}".rstrip("0").rstrip(".")
    return text or "0"


@login_required
def bill_create(request):
    # Suppliers are bought from on supplier bills rather than sold to, and an
    # inactive account should not be taking new business.
    customers = Customer.objects.filter(is_active=True, is_supplier=False)
    return render(request, "core/bill_create.html", {"customers": customers})


@require_GET
@login_required
def bill_products(request, customer_id):
    """What this customer can be billed for, at their own price.

    Feeds the step 1 product table. Inactive and out-of-stock products are
    left out entirely: neither can go on a new bill.
    """
    customer = Customer.objects.filter(pk=customer_id).first()
    if customer is None:
        return JsonResponse({"error": "That customer no longer exists."}, status=404)

    # One query for this customer's overrides, then one for the products. The
    # alternative is a CustomerPrice lookup per row.
    #
    # order_by() clears CustomerPrice.Meta.ordering, which sorts by customer
    # and product name and so drags both tables into a join this lookup has no
    # use for — the rows land in a dict either way.
    overrides = dict(
        CustomerPrice.objects.filter(customer=customer)
        .order_by()
        .values_list("product_id", "unit_price")
    )

    products = []
    for product in Product.objects.filter(is_active=True, qty__gt=0):
        override = overrides.get(product.pk)
        unit_price = product.default_price if override is None else override
        products.append(
            {
                "id": product.pk,
                "name": product.name,
                "size": product.size,
                "qty": _qty_text(product.qty),
                "unit_price": f"{unit_price:.2f}",
                "has_custom_price": override is not None,
            }
        )

    # safe=False: the agreed contract is a bare array.
    return JsonResponse(products, safe=False)


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
def ledger_index(request):
    # The sidebar's Customer Ledger entry. The ledger itself is per-customer,
    # at core:customer_ledger — this is still a placeholder section index.
    return render(request, "core/placeholder.html", {"section": "Customer Ledger"})


@login_required
def sales_report(request):
    return render(request, "core/placeholder.html", {"section": "Sales Report"})
