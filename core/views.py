import json
from datetime import timedelta
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

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
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone
from django.utils.dateparse import parse_date
from django.utils.formats import date_format
from django.views.decorators.http import require_GET, require_POST

from .decorators import super_admin_required
from .forms import (
    CashDrawerOutForm,
    CategoryForm,
    ChequeForm,
    CustomerForm,
    CustomerPriceForm,
    ProductForm,
    ProductionEntryForm,
    ProductQuickForm,
    SupplierQuickForm,
)
from .models import (
    Bill,
    BillItem,
    CashDrawer,
    CashTransfer,
    Category,
    Cheque,
    Customer,
    CustomerPrice,
    Payment,
    Product,
    ProductionEntry,
    SupplierBill,
    SupplierBillItem,
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


def _warning_signature(today, cheques):
    """A key for one day's set of cheque warnings.

    The dashboard card can be dismissed, but a warning about money that hasn't
    arrived should not stay dismissed for ever. Keying the dismissal to the day
    and the exact cheques means it comes back tomorrow, and immediately if a
    different cheque starts maturing.
    """
    # Sorted, so the key identifies the set rather than the order it was listed
    # in — re-sorting the same warnings must not resurrect a dismissal.
    ids = ",".join(str(pk) for pk in sorted(cheque.pk for cheque in cheques))
    return f"{today.isoformat()}:{ids}"


def _cash_drawer_balance(queryset=None):
    """Net cash on hand: 'in' adds, 'out' and 'transfer' both remove.

    Takes a queryset so the same rule can price a slice of the log — the
    opening balance of a date range is this over everything before it.
    """
    if queryset is None:
        queryset = CashDrawer.objects.all()
    return queryset.aggregate(
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

    # Pending only: a held cheque is one we have chosen not to bank yet, and a
    # deposited one is finished with. No lower bound either — a cheque that
    # matured last week and still has not been banked is the most urgent row
    # on the page, not one to hide.
    maturing_cheques = (
        Cheque.objects.filter(maturity_date__lte=horizon, status=Cheque.Status.PENDING)
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
            # Identifies exactly this set of warnings on this day, so a dismissal
            # lasts until the warnings actually change rather than for ever.
            "cheque_signature": _warning_signature(today, maturing_cheques),
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
        # A cheque that bounced or is being held never became money, and the
        # customer's balance has had it taken back off. Leaving the payment
        # here would walk the running balance away from the account itself.
        .exclude(
            cheques__status__in=[Cheque.Status.BOUNCED, Cheque.Status.HELD]
        )
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
    customers = list(Customer.objects.filter(is_active=True, is_supplier=False))
    for customer in customers:
        # A new bill prices against the balance as it stands. The edit page
        # sets this differently, which is the only difference between them.
        customer.balance_for_bill = customer.balance

    return render(
        request,
        "core/bill_create.html",
        {
            "customers": customers,
            "today": timezone.localdate(),
            # Straight off the models, so the radio values and account codes the
            # page posts are the ones the save step will store.
            "payment_types": Bill.PaymentType.choices,
            "account_choices": Payment.Account.choices,
            "save_url": reverse("core:bill_save"),
            "is_edit": False,
        },
    )


@require_GET
@login_required
def bill_products(request, customer_id):
    """What this customer can be billed for, at their own price.

    Feeds the step 1 product table. Inactive and out-of-stock products are
    left out entirely: neither can go on a new bill.

    ?bill=<id> asks the same question for an edit, where the bill's own lines
    have already taken their stock. Those quantities are added back and the
    products kept in the list however low they have run, otherwise a bill that
    cleared the shelf could never be edited.
    """
    customer = Customer.objects.filter(pk=customer_id).first()
    if customer is None:
        return JsonResponse({"error": "That customer no longer exists."}, status=404)

    editing = request.GET.get("bill", "").strip()
    held = {}
    if editing.isdigit():
        held = {
            item.product_id: item.qty
            for item in BillItem.objects.filter(bill_id=int(editing))
        }

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

    sellable = Product.objects.filter(
        Q(is_active=True, qty__gt=0) | Q(pk__in=list(held))
    )

    products = []
    for product in sellable:
        override = overrides.get(product.pk)
        unit_price = product.default_price if override is None else override
        # Stock this bill is already holding is stock this bill may still use.
        available = product.qty + held.get(product.pk, ZERO)
        products.append(
            {
                "id": product.pk,
                "name": product.name,
                "size": product.size,
                "qty": _qty_text(available),
                "unit_price": f"{unit_price:.2f}",
                "has_custom_price": override is not None,
            }
        )

    # safe=False: the agreed contract is a bare array.
    return JsonResponse(products, safe=False)


class BillError(Exception):
    """A reason to roll the save back, worded for the biller."""


def _decimal(raw, label, places):
    """Parse one money or quantity figure off the payload."""
    text = str(raw if raw is not None else "").strip()
    if text == "":
        raise BillError(f"{label} is required.")
    try:
        value = Decimal(text)
    except InvalidOperation:
        raise BillError(f"{label} must be a number.")
    if not value.is_finite():
        raise BillError(f"{label} must be a number.")
    if value < 0:
        raise BillError(f"{label} cannot be negative.")

    step = Decimal("0.01") if places == 2 else Decimal("0.001")
    return value.quantize(step, rounding=ROUND_HALF_UP)


def _optional_decimal(raw, label):
    """Blank means nothing was tendered on this leg, not zero-as-an-error."""
    if raw is None or str(raw).strip() == "":
        return ZERO
    return _decimal(raw, label, 2)


def _read_cheque(raw, required):
    """The cheque leg, or None when an optional cheque is left blank."""
    raw = raw or {}
    amount_text = str(raw.get("amount") or "").strip()
    if not required and amount_text == "":
        return None

    amount = _decimal(amount_text, "Cheque amount", 2)
    if amount <= ZERO:
        raise BillError("Cheque amount must be above 0.")

    cheque_no = str(raw.get("cheque_no") or "").strip()
    bank_name = str(raw.get("bank_name") or "").strip()
    if not cheque_no:
        raise BillError("Cheque number is required.")
    if not bank_name:
        raise BillError("Bank name is required.")

    received = _parse_date(raw.get("received_date"))
    maturity = _parse_date(raw.get("maturity_date"))
    if received is None:
        raise BillError("Cheque received date is required.")
    if maturity is None:
        raise BillError("Cheque maturity date is required.")
    if maturity < received:
        raise BillError("Cheque maturity date cannot be before the received date.")

    return {
        "cheque_no": cheque_no,
        "bank_name": bank_name,
        "branch": str(raw.get("branch") or "").strip(),
        "acc_no": str(raw.get("acc_no") or "").strip(),
        "amount": amount,
        "received_date": received,
        "maturity_date": maturity,
    }


def _read_payment(raw, subtotal, customer):
    """Re-derive every payment leg from the payload.

    The page validates all of this already; none of that is evidence, so it is
    all recomputed here from the customer's stored balance.
    """
    raw = raw or {}
    kind = str(raw.get("type") or "").strip()
    valid = {value for value, _ in Bill.PaymentType.choices}
    if kind not in valid:
        raise BillError("Choose a payment type.")

    accounts = {value for value, _ in Payment.Account.choices}
    parts = {
        "type": kind,
        "cash": ZERO,
        "cash_account": "",
        "transfer": ZERO,
        "transfer_account": "",
        "cheque": None,
    }

    if kind == Bill.PaymentType.FULL_CASH:
        parts["cash"] = _decimal(raw.get("cash"), "Amount received", 2)
        account = str(raw.get("account") or "").strip()
        if account and account not in accounts:
            raise BillError("Choose a valid account.")
        parts["cash_account"] = account

    elif kind == Bill.PaymentType.FULL_CHEQUE:
        parts["cheque"] = _read_cheque(raw.get("cheque"), required=True)

    elif kind == Bill.PaymentType.PARTIAL:
        parts["cash"] = _decimal(raw.get("cash"), "Cash amount", 2)
        parts["cheque"] = _read_cheque(raw.get("cheque"), required=True)

    elif kind == Bill.PaymentType.MIXED:
        parts["cash"] = _optional_decimal(raw.get("cash"), "Cash amount")
        parts["transfer"] = _optional_decimal(raw.get("transfer"), "Transfer amount")
        parts["cheque"] = _read_cheque(raw.get("cheque"), required=False)
        if parts["transfer"] > ZERO:
            account = str(raw.get("account") or "").strip()
            if account not in accounts:
                raise BillError("Choose an account for the transfer.")
            parts["transfer_account"] = account

    cheque_amount = parts["cheque"]["amount"] if parts["cheque"] else ZERO
    paid = parts["cash"] + parts["transfer"] + cheque_amount

    if kind == Bill.PaymentType.MIXED and paid <= ZERO:
        raise BillError("Enter at least one payment amount.")

    # What settles everything: this bill plus any debt, less any credit. A
    # negative balance is debt and a positive one is credit, so both are the
    # one subtraction.
    target = (subtotal - customer.balance).quantize(Decimal("0.01"))

    if kind == Bill.PaymentType.PAY_LATER:
        paid = ZERO
    elif target <= ZERO:
        raise BillError("Nothing to collect — the credit covers this bill. Use Pay Later.")
    elif paid != target:
        raise BillError(f"Payment must total {target:.2f} — got {paid:.2f}.")

    parts["paid"] = paid
    return parts


def _check_credit_limit(customer, subtotal, parts, user):
    """Only Pay Later can leave money outstanding; every other type is held to
    the full amount, so it lands the balance on zero."""
    if parts["type"] != Bill.PaymentType.PAY_LATER:
        return

    after = customer.balance - subtotal
    owed = -after if after < ZERO else ZERO
    if owed <= customer.credit_limit:
        return

    # The page hides the override from managers, which is a courtesy, not a
    # control: the flag arrives from the browser and is only worth what this
    # check makes it worth.
    if not _is_super_admin(user):
        raise BillError(
            f"This bill leaves {owed:.2f} outstanding, past the "
            f"{customer.credit_limit:.2f} credit limit. A super admin has to approve it."
        )
    if not parts.get("credit_override"):
        raise BillError(
            f"This bill leaves {owed:.2f} outstanding, past the "
            f"{customer.credit_limit:.2f} credit limit, and needs an override."
        )


def _record_payments(bill, customer, parts):
    """Payment rows, and the paper trail each one leaves behind."""
    now = timezone.now()

    if parts["cash"] > ZERO:
        payment = Payment.objects.create(
            bill=bill,
            method=Payment.Method.CASH,
            amount=parts["cash"],
            account=parts["cash_account"],
            paid_at=now,
        )
        if parts["cash_account"]:
            CashTransfer.objects.create(
                payment=payment,
                to_account=parts["cash_account"],
                amount=parts["cash"],
                transferred_at=now,
            )
            # Both legs, deliberately. The cash reached the drawer and then
            # left it for the bank; writing only the transfer would take the
            # drawer down by money it never held.
            CashDrawer.objects.create(
                txn_date=bill.bill_date,
                txn_type=CashDrawer.TxnType.IN,
                amount=parts["cash"],
                reason=f"Bill #{bill.pk} cash",
                bill=bill,
            )
            CashDrawer.objects.create(
                txn_date=bill.bill_date,
                txn_type=CashDrawer.TxnType.TRANSFER,
                amount=parts["cash"],
                reason=f"Bill #{bill.pk} cash to {payment.get_account_display()}",
                bill=bill,
            )
        else:
            CashDrawer.objects.create(
                txn_date=bill.bill_date,
                txn_type=CashDrawer.TxnType.IN,
                amount=parts["cash"],
                reason=f"Bill #{bill.pk} cash",
                bill=bill,
            )

    if parts["transfer"] > ZERO:
        payment = Payment.objects.create(
            bill=bill,
            method=Payment.Method.TRANSFER,
            amount=parts["transfer"],
            account=parts["transfer_account"],
            paid_at=now,
        )
        CashTransfer.objects.create(
            payment=payment,
            to_account=parts["transfer_account"],
            amount=parts["transfer"],
            transferred_at=now,
        )
        # No drawer rows: this money went bank to bank without passing through
        # the till.

    cheque = parts["cheque"]
    if cheque:
        payment = Payment.objects.create(
            bill=bill,
            method=Payment.Method.CHEQUE,
            amount=cheque["amount"],
            paid_at=now,
        )
        Cheque.objects.create(
            payment=payment,
            customer=customer,
            cheque_no=cheque["cheque_no"],
            bank_name=cheque["bank_name"],
            branch=cheque["branch"],
            acc_no=cheque["acc_no"],
            amount=cheque["amount"],
            received_date=cheque["received_date"],
            maturity_date=cheque["maturity_date"],
        )


def _reverse_bill(bill):
    """Undo everything a bill did, leaving it an empty header.

    Shared by edit and delete: an edit is this followed by a fresh write, and a
    delete is this followed by dropping the header. Must run inside a
    transaction — half a reversal is worse than none.
    """
    # Stock first, off the rows about to be deleted.
    for item in bill.items.all():
        Product.objects.filter(pk=item.product_id).update(qty=F("qty") + item.qty)

    # new_balance = old_balance + balance_change on the way in, so taking the
    # same figure back out is the exact inverse.
    Customer.objects.filter(pk=bill.customer_id).update(
        balance=F("balance") - bill.balance_change
    )

    # CashDrawer.bill is SET_NULL, so these survive the bill as orphans that
    # still count toward the drawer balance. They have to go by hand.
    CashDrawer.objects.filter(bill=bill).delete()

    # Cheque and CashTransfer hang off Payment with CASCADE, so they go too.
    bill.payments.all().delete()
    bill.items.all().delete()


@transaction.atomic
def _save_bill(user, payload):
    """Write one bill and everything it touches, or nothing at all."""
    return _write_bill(Bill(), user, payload)


@transaction.atomic
def _update_bill(bill, user, payload):
    """Rewrite a bill as if it had always said this.

    The reversal has to come first: the new lines are validated against stock
    and a balance that no longer carry this bill's own effects, so re-saving an
    unchanged bill is a no-op rather than a double charge.
    """
    _reverse_bill(bill)
    return _write_bill(bill, user, payload)


def _write_bill(bill, user, payload):
    # Read fresh: on an edit the reversal above moved the balance with an F()
    # expression, which leaves any object already in memory stale.
    customer = (
        Customer.objects.filter(
            pk=payload.get("customer_id"), is_active=True, is_supplier=False
        )
        .first()
    )
    if customer is None:
        raise BillError("That customer can't be billed.")

    raw_lines = payload.get("lines")
    if not isinstance(raw_lines, list) or not raw_lines:
        raise BillError("Add at least one product to the bill.")

    products = {
        product.pk: product
        for product in Product.objects.filter(
            pk__in=[raw.get("product_id") for raw in raw_lines if isinstance(raw, dict)],
            is_active=True,
        )
    }
    quoted = dict(
        CustomerPrice.objects.filter(customer=customer)
        .order_by()
        .values_list("product_id", "unit_price")
    )

    items = []
    subtotal = ZERO
    seen = set()
    for raw in raw_lines:
        if not isinstance(raw, dict):
            raise BillError(MALFORMED)

        product = products.get(raw.get("product_id"))
        if product is None:
            raise BillError("A product on this bill is no longer available.")
        if product.pk in seen:
            raise BillError(f"{product} is on the bill twice.")
        seen.add(product.pk)

        qty = _decimal(raw.get("qty"), "Quantity", 3)
        if qty <= ZERO:
            raise BillError(f"Quantity for {product} must be above 0.")
        unit_price = _decimal(raw.get("unit_price"), "Unit price", 2)

        # Recomputed here: a line total off the browser is a number the biller
        # could have typed.
        line_total = (qty * unit_price).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        subtotal += line_total
        items.append(
            {
                "product": product,
                "qty": qty,
                "unit_price": unit_price,
                "line_total": line_total,
                "quoted": quoted.get(product.pk, product.default_price),
            }
        )

    total = subtotal
    parts = _read_payment(payload.get("payment"), subtotal, customer)
    parts["credit_override"] = bool((payload.get("payment") or {}).get("credit_override"))
    _check_credit_limit(customer, subtotal, parts, user)

    paid = parts["paid"]
    if paid >= total:
        status = Bill.Status.PAID
    elif paid > ZERO:
        status = Bill.Status.PARTIAL
    else:
        status = Bill.Status.UNPAID

    # How this bill moves the balance. The sale is debt (balance down), the
    # payment settles debt (balance up), so the two net to paid - total. Adding
    # this to the balance is the whole update, which keeps
    # new_balance = old_balance + balance_change true — what the field is for.
    balance_change = paid - total

    # 1. header. An edit keeps the date it was billed on — the goods left the
    # yard that day whatever gets corrected afterwards.
    bill.customer = customer
    if bill.pk is None:
        bill.bill_date = timezone.localdate()
    bill.subtotal = subtotal
    bill.total_amount = total
    bill.paid_amount = paid
    bill.balance_change = balance_change
    bill.payment_type = parts["type"]
    bill.status = status
    bill.notes = str(payload.get("notes") or "").strip()
    bill.save()

    # 2. lines, and the stock they take with them
    for item in items:
        BillItem.objects.create(
            bill=bill,
            product=item["product"],
            qty=item["qty"],
            unit_price=item["unit_price"],
            line_total=item["line_total"],
        )
        # Guarded update rather than read-then-write: the filter and the
        # decrement land in one statement, so two tills can't both sell the
        # last unit. Zero rows updated means the stock moved under us.
        moved = Product.objects.filter(
            pk=item["product"].pk, qty__gte=item["qty"]
        ).update(qty=F("qty") - item["qty"])
        if not moved:
            product = Product.objects.get(pk=item["product"].pk)
            raise BillError(
                f"Not enough stock for {product} — {product.qty:.3f} left, "
                f"{item['qty']:.3f} needed."
            )

    # 3. money
    _record_payments(bill, customer, parts)

    # 4. balance. F() so a balance moved by another till in the meantime is
    # adjusted rather than overwritten.
    Customer.objects.filter(pk=customer.pk).update(balance=F("balance") + balance_change)

    # 5. prices the biller changed become this customer's price. Compared
    # against what was quoted, not against the browser's price_changed flag.
    for item in items:
        if item["unit_price"] != item["quoted"]:
            CustomerPrice.objects.update_or_create(
                customer=customer,
                product=item["product"],
                defaults={"unit_price": item["unit_price"]},
            )

    return bill


@require_POST
@login_required
def bill_save(request):
    try:
        payload = json.loads(request.body or b"{}")
    except json.JSONDecodeError:
        return JsonResponse({"success": False, "error": MALFORMED}, status=400)
    if not isinstance(payload, dict):
        return JsonResponse({"success": False, "error": MALFORMED}, status=400)

    try:
        bill = _save_bill(request.user, payload)
    except BillError as exc:
        # _save_bill is atomic, so nothing it wrote survives this.
        return JsonResponse({"success": False, "error": str(exc)}, status=400)

    messages.success(
        request, f"Bill #{bill.pk} for {bill.customer.name} was saved."
    )
    return JsonResponse(
        {
            "success": True,
            "bill_id": bill.pk,
            "redirect": reverse("core:bill_detail", args=[bill.pk]),
        }
    )


@login_required
def bill_detail(request, pk):
    bill = get_object_or_404(_bills_with_counts(), pk=pk)
    return render(
        request,
        "core/bill_detail.html",
        {
            "bill": bill,
            "reverses": json.dumps(_reversal_summary(bill)),
            "items": bill.items.select_related("product"),
            "payments": bill.payments.prefetch_related("cheques", "transfers"),
            # The bill records how it moved the balance, so the reading at the
            # time it was saved can be recovered without a full ledger replay.
            "balance_before": bill.customer.balance - bill.balance_change,
            # Templates can't take an absolute value, and a debt reads better
            # as a positive figure.
            "owed_now": -bill.customer.balance if bill.customer.balance < ZERO else ZERO,
        },
    )


def _bills_with_counts():
    """Bills carrying what the delete modal has to describe."""
    return Bill.objects.select_related("customer").annotate(
        # distinct=True: without it these joins multiply each other's rows.
        item_count=Count("items", distinct=True),
        payment_count=Count("payments", distinct=True),
        drawer_count=Count("cash_drawer_entries", distinct=True),
    )


def _reversal_summary(bill):
    """Plain sentences for what undoing this bill puts back.

    Built here rather than in the modal so the warning and the reversal are
    read off the same numbers.
    """
    lines = []

    if bill.item_count:
        lines.append(
            f"{bill.item_count} line{'' if bill.item_count == 1 else 's'} "
            f"of stock returned"
        )

    if bill.balance_change:
        restored = bill.customer.balance - bill.balance_change
        lines.append(f"{bill.customer.name}'s balance returns to {restored:.2f}")

    if bill.payment_count:
        lines.append(
            f"{bill.payment_count} payment record{'' if bill.payment_count == 1 else 's'} "
            f"removed, with any cheque or transfer on them"
        )

    if bill.drawer_count:
        lines.append(
            f"{bill.drawer_count} cash drawer "
            f"entr{'y' if bill.drawer_count == 1 else 'ies'} removed"
        )

    return lines or ["Nothing — this bill moved no stock or money."]


def _bill_initial(bill):
    """The bill as the create page's own payload shape, for rehydrating it."""
    return {
        "customer_id": bill.customer_id,
        "lines": [
            {
                "product_id": item.product_id,
                "qty": f"{item.qty:.3f}".rstrip("0").rstrip("."),
                "unit_price": f"{item.unit_price:.2f}",
            }
            for item in bill.items.all()
        ],
        "payment": _payment_initial(bill),
    }


def _payment_initial(bill):
    """Unpick the payment rows back into the form's fields."""
    payment = {"type": bill.payment_type}

    for row in bill.payments.prefetch_related("cheques"):
        if row.method == Payment.Method.CASH:
            payment["cash"] = f"{row.amount:.2f}"
            payment["account"] = row.account
        elif row.method == Payment.Method.TRANSFER:
            payment["transfer"] = f"{row.amount:.2f}"
            payment["account"] = row.account
        elif row.method == Payment.Method.CHEQUE:
            cheque = row.cheques.first()
            if cheque:
                payment["cheque"] = {
                    "cheque_no": cheque.cheque_no,
                    "bank_name": cheque.bank_name,
                    "branch": cheque.branch,
                    "acc_no": cheque.acc_no,
                    "amount": f"{cheque.amount:.2f}",
                    "received_date": cheque.received_date.isoformat(),
                    "maturity_date": cheque.maturity_date.isoformat(),
                }
    return payment


@login_required
def bill_edit(request, pk):
    bill = get_object_or_404(Bill.objects.select_related("customer"), pk=pk)

    if request.method == "POST":
        try:
            payload = json.loads(request.body or b"{}")
        except json.JSONDecodeError:
            return JsonResponse({"success": False, "error": MALFORMED}, status=400)
        if not isinstance(payload, dict):
            return JsonResponse({"success": False, "error": MALFORMED}, status=400)

        try:
            bill = _update_bill(bill, request.user, payload)
        except BillError as exc:
            # _update_bill is atomic, so the reversal it started is undone too.
            return JsonResponse({"success": False, "error": str(exc)}, status=400)

        messages.success(request, f"Bill #{bill.pk} was updated.")
        return JsonResponse(
            {
                "success": True,
                "bill_id": bill.pk,
                "redirect": reverse("core:bill_detail", args=[bill.pk]),
            }
        )

    # The page prices this bill as though it had never been saved, so the
    # customer it belongs to is offered the balance it would have without it.
    # Every other customer's balance is already free of this bill.
    customers = list(Customer.objects.filter(is_active=True, is_supplier=False))
    if bill.customer not in customers:
        # Retired or turned supplier since; still has to be editable.
        customers.insert(0, bill.customer)
    for customer in customers:
        customer.balance_for_bill = (
            customer.balance - bill.balance_change
            if customer.pk == bill.customer_id
            else customer.balance
        )

    return render(
        request,
        "core/bill_edit.html",
        {
            "bill": bill,
            "customers": customers,
            "today": timezone.localdate(),
            "payment_types": Bill.PaymentType.choices,
            "account_choices": Payment.Account.choices,
            "save_url": reverse("core:bill_edit", args=[bill.pk]),
            "initial": _bill_initial(bill),
            "is_edit": True,
        },
    )


@require_POST
@super_admin_required
def bill_delete(request, pk):
    bill = get_object_or_404(Bill.objects.select_related("customer"), pk=pk)
    label = f"Bill #{bill.pk}"
    customer = bill.customer.name

    with transaction.atomic():
        _reverse_bill(bill)
        bill.delete()

    messages.success(request, f"{label} for {customer} was deleted and reversed.")
    return redirect("core:bill_list")


@login_required
def bill_list(request):
    from_date = _parse_date(request.GET.get("from_date"))
    to_date = _parse_date(request.GET.get("to_date"))

    customer_id = request.GET.get("customer", "").strip()
    selected_customer = int(customer_id) if customer_id.isdigit() else None

    payment_type = request.GET.get("payment_type", "").strip()
    if payment_type not in {value for value, _ in Bill.PaymentType.choices}:
        payment_type = ""
    status = request.GET.get("status", "").strip()
    if status not in {value for value, _ in Bill.Status.choices}:
        status = ""

    bills = _bills_with_counts().annotate(
        # paid_amount can run past total_amount when a payment also clears old
        # debt, and a bill can't owe less than nothing.
        outstanding=Greatest(
            F("total_amount") - F("paid_amount"), Value(ZERO), output_field=MONEY
        )
    )

    if from_date:
        bills = bills.filter(bill_date__gte=from_date)
    if to_date:
        bills = bills.filter(bill_date__lte=to_date)
    if selected_customer:
        bills = bills.filter(customer_id=selected_customer)
    if payment_type:
        bills = bills.filter(payment_type=payment_type)
    if status:
        bills = bills.filter(status=status)

    bills = list(bills)
    for bill in bills:
        bill.reverses = json.dumps(_reversal_summary(bill))

    return render(
        request,
        "core/bill_list.html",
        {
            "bills": bills,
            "customers": Customer.objects.filter(is_supplier=False),
            "from_date": from_date,
            "to_date": to_date,
            "selected_customer": selected_customer,
            "payment_type": payment_type,
            "status": status,
            "payment_types": Bill.PaymentType.choices,
            "statuses": Bill.Status.choices,
            "is_filtered": bool(
                from_date or to_date or selected_customer or payment_type or status
            ),
            "total_count": Bill.objects.count(),
        },
    )


# ------------------------------------------------------------------ cheques
# A cheque is money we are counting on but do not have. Pending and deposited
# both mean we still expect it, so the customer keeps the credit. Held and
# bounced mean we don't, so the debt comes back.

#: Statuses where the cheque's amount is still credited to the customer.
CREDITED_CHEQUE_STATUSES = {Cheque.Status.PENDING, Cheque.Status.DEPOSITED}


def _cheque_credit(status, amount):
    """What a cheque in this state contributes to its customer's balance."""
    return amount if status in CREDITED_CHEQUE_STATUSES else ZERO


def _move_balance_for_cheque(cheque, was_status, was_amount):
    """Move the customer's balance by the difference the change makes.

    One rule covers every transition, including an amount correction:

        pending  -> deposited   nothing moves, we still expect the money
        pending  -> held        credit comes off, the debt is back
        deposited-> bounced     same, even though it had cleared
        bounced  -> pending     re-presented, credit goes back on
        amount 100 -> 150       while credited, 50 more is owed to us

    Returns the signed move, for the message.
    """
    delta = _cheque_credit(cheque.status, cheque.amount) - _cheque_credit(
        was_status, was_amount
    )
    if delta:
        # F() so a balance moved elsewhere in the meantime is adjusted rather
        # than overwritten.
        Customer.objects.filter(pk=cheque.customer_id).update(
            balance=F("balance") + delta
        )
    return delta


def _cheque_balance_note(cheque, delta):
    """Say what the balance did, since the operator can't see it happen."""
    if not delta:
        return ""
    cheque.customer.refresh_from_db()
    if delta < 0:
        return (
            f" {cheque.customer.name} owes {abs(delta):.2f} again — "
            f"balance is now {cheque.customer.balance:.2f}."
        )
    return (
        f" {cheque.customer.name} is credited {delta:.2f} — "
        f"balance is now {cheque.customer.balance:.2f}."
    )


def _set_cheque_status(request, pk, status, bounce_new_date=None):
    cheque = get_object_or_404(Cheque.objects.select_related("customer"), pk=pk)
    was_status, was_amount = cheque.status, cheque.amount

    if cheque.status == status:
        messages.info(
            request,
            f"Cheque {cheque.cheque_no} is already {cheque.get_status_display().lower()}.",
        )
        return redirect("core:cheque_list")

    fields = ["status"]
    with transaction.atomic():
        cheque.status = status
        if bounce_new_date is not None:
            cheque.bounce_new_date = bounce_new_date
            fields.append("bounce_new_date")
        cheque.save(update_fields=fields)
        delta = _move_balance_for_cheque(cheque, was_status, was_amount)

    messages.success(
        request,
        f"Cheque {cheque.cheque_no} marked {cheque.get_status_display().lower()}."
        + _cheque_balance_note(cheque, delta),
    )
    return redirect("core:cheque_list")


@require_POST
@login_required
def cheque_deposit(request, pk):
    # Deliberately no balance change: the credit went on when the cheque was
    # taken, and clearing the bank only confirms it.
    return _set_cheque_status(request, pk, Cheque.Status.DEPOSITED)


@require_POST
@login_required
def cheque_hold(request, pk):
    return _set_cheque_status(request, pk, Cheque.Status.HELD)


@require_POST
@login_required
def cheque_bounce(request, pk):
    new_date = _parse_date(request.POST.get("bounce_new_date"))
    if new_date is None:
        messages.error(
            request, "Enter the date the cheque is expected to be re-presented."
        )
        return redirect("core:cheque_list")
    return _set_cheque_status(request, pk, Cheque.Status.BOUNCED, new_date)


@require_POST
@super_admin_required
def cheque_delete(request, pk):
    """Remove a cheque that should never have been recorded.

    Not a bounce — that is a real event with its own status. This is for a
    cheque entered in error, so it takes the payment with it.
    """
    cheque = get_object_or_404(Cheque.objects.select_related("customer"), pk=pk)

    if cheque.status == Cheque.Status.DEPOSITED:
        messages.error(
            request,
            f"Cheque {cheque.cheque_no} has been deposited, so it can't be deleted. "
            f"The money is in the bank — mark it bounced if it came back.",
        )
        return redirect("core:cheque_list")

    number = cheque.cheque_no
    customer = cheque.customer

    with transaction.atomic():
        # Only a credited cheque has anything to take back; a held or bounced
        # one was already reversed when it got that status.
        delta = -_cheque_credit(cheque.status, cheque.amount)
        if delta:
            Customer.objects.filter(pk=customer.pk).update(balance=F("balance") + delta)

        # The cheque hangs off the payment by CASCADE, so removing the payment
        # removes both — the payment only ever existed to carry this cheque.
        payment = cheque.payment
        payment.delete()

    customer.refresh_from_db()
    note = (
        f" {customer.name} owes {abs(delta):.2f} again — "
        f"balance is now {customer.balance:.2f}."
        if delta
        else ""
    )
    messages.success(request, f"Cheque {number} was deleted." + note)
    return redirect("core:cheque_list")


@login_required
def cheque_edit(request, pk):
    cheque = get_object_or_404(Cheque.objects.select_related("customer"), pk=pk)
    was_status, was_amount = cheque.status, cheque.amount

    form = ChequeForm(request.POST or None, instance=cheque)
    if request.method == "POST" and form.is_valid():
        with transaction.atomic():
            cheque = form.save()
            delta = _move_balance_for_cheque(cheque, was_status, was_amount)

        messages.success(
            request,
            f"Cheque {cheque.cheque_no} was updated." + _cheque_balance_note(cheque, delta),
        )
        return redirect("core:cheque_list")

    return render(
        request,
        "core/cheque_edit.html",
        {"form": form, "cheque": cheque, "credited": was_status in CREDITED_CHEQUE_STATUSES},
    )


@login_required
def cheque_list(request):
    today = timezone.localdate()
    horizon = today + timedelta(days=CHEQUE_WARNING_DAYS)

    status = request.GET.get("status", "").strip()
    if status not in {value for value, _ in Cheque.Status.choices}:
        status = ""

    customer_id = request.GET.get("customer", "").strip()
    selected_customer = int(customer_id) if customer_id.isdigit() else None

    from_date = _parse_date(request.GET.get("from_date"))
    to_date = _parse_date(request.GET.get("to_date"))

    cheques = Cheque.objects.select_related("customer")
    if status:
        cheques = cheques.filter(status=status)
    if selected_customer:
        cheques = cheques.filter(customer_id=selected_customer)
    if from_date:
        cheques = cheques.filter(maturity_date__gte=from_date)
    if to_date:
        cheques = cheques.filter(maturity_date__lte=to_date)

    cheques = list(cheques)
    for cheque in cheques:
        # Maturing on us and still not banked: the row the operator is meant
        # to act on today. Anything already overdue counts too.
        cheque.is_due_soon = (
            cheque.status == Cheque.Status.PENDING and cheque.maturity_date <= horizon
        )

    return render(
        request,
        "core/cheque_list.html",
        {
            "cheques": cheques,
            "customers": Customer.objects.filter(cheques__isnull=False).distinct(),
            "status": status,
            "selected_customer": selected_customer,
            "from_date": from_date,
            "to_date": to_date,
            "statuses": Cheque.Status.choices,
            "is_filtered": bool(status or selected_customer or from_date or to_date),
            "total_count": Cheque.objects.count(),
            "due_count": sum(1 for cheque in cheques if cheque.is_due_soon),
            "warning_days": CHEQUE_WARNING_DAYS,
            "today": today,
        },
    )


# -------------------------------------------------------------- cash drawer
# Money comes in only by saving a bill; this page is where it leaves.


def _account_banked(account):
    """What bill payments have put into one account.

    Read off CashTransfer, which is the only place an account is recorded.
    Manual transfers on this page are CashDrawer rows with no account column,
    so they lower the drawer without ever reaching this figure.
    """
    return CashTransfer.objects.filter(to_account=account).aggregate(
        total=Coalesce(Sum("amount"), ZERO, output_field=MONEY)
    )["total"]


@login_required
def cash_drawer(request):
    balance = _cash_drawer_balance()

    form = CashDrawerOutForm(
        request.POST or None,
        drawer_balance=balance,
        initial={"txn_date": timezone.localdate()},
    )
    if request.method == "POST":
        if form.is_valid():
            entry = form.save()
            messages.success(
                request,
                f"{entry.reason} — {entry.amount:,.2f} out of the drawer. "
                f"{_cash_drawer_balance():,.2f} left.",
            )
            return redirect("core:cash_drawer")
        messages.error(request, "That entry couldn't be saved — see the form.")

    from_date = _parse_date(request.GET.get("from_date"))
    to_date = _parse_date(request.GET.get("to_date"))

    entries = CashDrawer.objects.select_related("bill", "bill__customer")
    if from_date:
        entries = entries.filter(txn_date__gte=from_date)
    if to_date:
        entries = entries.filter(txn_date__lte=to_date)

    # Oldest first: a running balance read newest-first counts backwards.
    entries = entries.order_by("txn_date", "id")

    # Everything before the range still happened, so the running column starts
    # where the drawer actually stood — not at zero.
    opening = (
        _cash_drawer_balance(CashDrawer.objects.filter(txn_date__lt=from_date))
        if from_date
        else ZERO
    )

    rows = []
    running = opening
    total_in = ZERO
    total_out = ZERO
    for entry in entries:
        is_in = entry.txn_type == CashDrawer.TxnType.IN
        running += entry.amount if is_in else -entry.amount
        if is_in:
            total_in += entry.amount
        else:
            total_out += entry.amount
        rows.append(
            {
                "entry": entry,
                "is_in": is_in,
                "running": running,
            }
        )

    return render(
        request,
        "core/cash_drawer.html",
        {
            "form": form,
            # The drawer as it stands now, whatever the filter shows.
            "balance": balance,
            "senovka_banked": _account_banked(CashTransfer.Account.SENOVKA),
            "dinusha_banked": _account_banked(CashTransfer.Account.DINUSHA),
            "rows": rows,
            "opening": opening,
            "closing": running,
            "total_in": total_in,
            "total_out": total_out,
            "from_date": from_date,
            "to_date": to_date,
            "is_filtered": bool(from_date or to_date),
            "total_count": CashDrawer.objects.count(),
            "kind_choices": CashDrawerOutForm.KIND_CHOICES,
        },
    )


# ----------------------------------------------------------- supplier bills
# The mirror of a sales bill: stock comes in and the balance moves the other
# way. A positive balance is what we owe them.


class SupplierBillError(BillError):
    """A reason to roll the save back, worded for the operator.

    Subclasses BillError because the two paths share their parsing helpers —
    _decimal raises BillError, and a supplier bill has to catch that as readily
    as its own complaints rather than let it escape as a 500.
    """


def _supplier_products():
    """Everything a supplier bill may receive, for the line dropdown."""
    return [
        {
            "id": product.pk,
            "name": product.name,
            "size": product.size,
            "label": str(product),
            "default_price": f"{product.default_price:.2f}",
        }
        for product in Product.objects.filter(is_active=True).order_by("name", "size")
    ]


def _read_supplier_lines(payload):
    raw_lines = payload.get("lines")
    if not isinstance(raw_lines, list) or not raw_lines:
        raise SupplierBillError("Add at least one product line.")

    products = {
        product.pk: product
        for product in Product.objects.filter(
            pk__in=[raw.get("product_id") for raw in raw_lines if isinstance(raw, dict)]
        )
    }

    items = []
    seen = set()
    for raw in raw_lines:
        if not isinstance(raw, dict):
            raise SupplierBillError(MALFORMED)

        product = products.get(raw.get("product_id"))
        if product is None:
            raise SupplierBillError("A product on this bill no longer exists.")
        if product.pk in seen:
            raise SupplierBillError(f"{product} is on the bill twice.")
        seen.add(product.pk)

        qty = _decimal(raw.get("qty"), "Quantity", 3)
        if qty <= ZERO:
            raise SupplierBillError(f"Quantity for {product} must be above 0.")
        unit_price = _decimal(raw.get("unit_price"), "Unit price", 2)

        # Recomputed: a line total off the browser is a number someone typed.
        line_total = (qty * unit_price).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        items.append(
            {
                "product": product,
                "qty": qty,
                "unit_price": unit_price,
                "line_total": line_total,
            }
        )
    return items


def _reverse_supplier_bill(bill):
    """Undo a supplier bill: stock back out, and the debt to them cancelled.

    Guarded, because received stock may already have been sold on. Taking it
    back out regardless would leave a product holding a negative quantity, and
    that product then vanishes from every sales screen.
    """
    for item in bill.items.all():
        moved = Product.objects.filter(
            pk=item.product_id, qty__gte=item.qty
        ).update(qty=F("qty") - item.qty)
        if not moved:
            product = Product.objects.get(pk=item.product_id)
            raise SupplierBillError(
                f"Can't reverse {product}: {item.qty:.3f} was received but only "
                f"{product.qty:.3f} is left, so some of it has been sold on."
            )

    Customer.objects.filter(pk=bill.supplier_id).update(
        balance=F("balance") - bill.total_amount
    )
    bill.items.all().delete()


def _write_supplier_bill(bill, payload):
    supplier = Customer.objects.filter(
        pk=payload.get("supplier_id"), is_supplier=True
    ).first()
    if supplier is None:
        raise SupplierBillError("Choose a supplier.")

    items = _read_supplier_lines(payload)
    total = sum((item["line_total"] for item in items), ZERO)

    # 1. header. An edit keeps the date the goods actually arrived.
    bill.supplier = supplier
    if bill.pk is None:
        bill.bill_date = timezone.localdate()
    bill.total_amount = total
    # Paying suppliers isn't built yet, so nothing has been paid on it.
    bill.paid_amount = ZERO
    bill.status = SupplierBill.Status.UNPAID
    bill.notes = str(payload.get("notes") or "").strip()
    bill.save()

    # 2. lines, and the stock they bring in
    for item in items:
        SupplierBillItem.objects.create(
            supplier_bill=bill,
            product=item["product"],
            qty=item["qty"],
            unit_price=item["unit_price"],
            line_total=item["line_total"],
        )
        Product.objects.filter(pk=item["product"].pk).update(qty=F("qty") + item["qty"])

    # 3. we owe them the lot. Positive is credit in their favour, so the sign
    # runs opposite to a sales bill. F() so a concurrent move is adjusted, not
    # overwritten.
    Customer.objects.filter(pk=supplier.pk).update(balance=F("balance") + total)

    return bill


@transaction.atomic
def _save_supplier_bill(payload):
    return _write_supplier_bill(SupplierBill(), payload)


@transaction.atomic
def _update_supplier_bill(bill, payload):
    _reverse_supplier_bill(bill)
    return _write_supplier_bill(bill, payload)


def _supplier_bill_payload(request):
    try:
        payload = json.loads(request.body or b"{}")
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


@require_POST
@login_required
def supplier_quick_create(request):
    """Create a supplier without leaving the bill form."""
    form = SupplierQuickForm(request.POST)
    if not form.is_valid():
        return JsonResponse({"success": False, "errors": form.errors}, status=400)

    supplier = form.save()
    return JsonResponse(
        {"success": True, "supplier": {"id": supplier.pk, "name": supplier.name}}
    )


@require_POST
@login_required
def product_quick_create(request):
    """Create a product without leaving the bill form."""
    form = ProductQuickForm(request.POST)
    if not form.is_valid():
        return JsonResponse({"success": False, "errors": form.errors}, status=400)

    product = form.save()
    return JsonResponse(
        {
            "success": True,
            "product": {
                "id": product.pk,
                "name": product.name,
                "size": product.size,
                "label": str(product),
                "default_price": f"{product.default_price:.2f}",
            },
        }
    )


def _supplier_bill_form_context(request, bill=None):
    return {
        "bill": bill,
        "suppliers": Customer.objects.filter(is_supplier=True),
        "products_json": _supplier_products(),
        "categories": Category.objects.all(),
        "product_form": ProductQuickForm(),
        "supplier_form": SupplierQuickForm(),
        "is_edit": bill is not None,
        "save_url": (
            reverse("core:supplier_bill_edit", args=[bill.pk])
            if bill
            else reverse("core:supplier_bill_create")
        ),
        "initial": (
            {
                "supplier_id": bill.supplier_id,
                "lines": [
                    {
                        "product_id": item.product_id,
                        "qty": f"{item.qty:.3f}".rstrip("0").rstrip("."),
                        "unit_price": f"{item.unit_price:.2f}",
                    }
                    for item in bill.items.all()
                ],
                "notes": bill.notes,
            }
            if bill
            else None
        ),
    }


@login_required
def supplier_bill_create(request):
    if request.method == "POST":
        payload = _supplier_bill_payload(request)
        if payload is None:
            return JsonResponse({"success": False, "error": MALFORMED}, status=400)
        try:
            bill = _save_supplier_bill(payload)
        except BillError as exc:
            return JsonResponse({"success": False, "error": str(exc)}, status=400)

        messages.success(
            request, f"Supplier bill #{bill.pk} for {bill.supplier.name} was saved."
        )
        return JsonResponse(
            {
                "success": True,
                "redirect": reverse("core:supplier_bill_detail", args=[bill.pk]),
            }
        )

    return render(
        request, "core/supplier_bill_create.html", _supplier_bill_form_context(request)
    )


@login_required
def supplier_bill_edit(request, pk):
    bill = get_object_or_404(SupplierBill.objects.select_related("supplier"), pk=pk)

    if request.method == "POST":
        payload = _supplier_bill_payload(request)
        if payload is None:
            return JsonResponse({"success": False, "error": MALFORMED}, status=400)
        try:
            bill = _update_supplier_bill(bill, payload)
        except BillError as exc:
            # Atomic, so the reversal it began is undone with it.
            return JsonResponse({"success": False, "error": str(exc)}, status=400)

        messages.success(request, f"Supplier bill #{bill.pk} was updated.")
        return JsonResponse(
            {
                "success": True,
                "redirect": reverse("core:supplier_bill_detail", args=[bill.pk]),
            }
        )

    return render(
        request, "core/supplier_bill_edit.html", _supplier_bill_form_context(request, bill)
    )


@require_POST
@super_admin_required
def supplier_bill_delete(request, pk):
    bill = get_object_or_404(SupplierBill.objects.select_related("supplier"), pk=pk)
    label = f"Supplier bill #{bill.pk}"
    supplier = bill.supplier.name

    try:
        with transaction.atomic():
            _reverse_supplier_bill(bill)
            bill.delete()
    except BillError as exc:
        messages.error(request, f"Cannot delete {label} — {exc}")
        return redirect("core:supplier_bill_detail", pk=pk)

    messages.success(request, f"{label} for {supplier} was deleted and reversed.")
    return redirect("core:supplier_bill_list")


@login_required
def supplier_bill_detail(request, pk):
    bill = get_object_or_404(
        SupplierBill.objects.select_related("supplier").annotate(
            item_count=Count("items")
        ),
        pk=pk,
    )
    return render(
        request,
        "core/supplier_bill_detail.html",
        {"bill": bill, "items": bill.items.select_related("product")},
    )


@login_required
def supplier_bill_list(request):
    from_date = _parse_date(request.GET.get("from_date"))
    to_date = _parse_date(request.GET.get("to_date"))

    supplier_id = request.GET.get("supplier", "").strip()
    selected_supplier = int(supplier_id) if supplier_id.isdigit() else None

    status = request.GET.get("status", "").strip()
    if status not in {value for value, _ in SupplierBill.Status.choices}:
        status = ""

    bills = SupplierBill.objects.select_related("supplier").annotate(
        item_count=Count("items")
    )
    if from_date:
        bills = bills.filter(bill_date__gte=from_date)
    if to_date:
        bills = bills.filter(bill_date__lte=to_date)
    if selected_supplier:
        bills = bills.filter(supplier_id=selected_supplier)
    if status:
        bills = bills.filter(status=status)

    return render(
        request,
        "core/supplier_bill_list.html",
        {
            "bills": bills,
            "suppliers": Customer.objects.filter(is_supplier=True),
            "from_date": from_date,
            "to_date": to_date,
            "selected_supplier": selected_supplier,
            "status": status,
            "statuses": SupplierBill.Status.choices,
            "is_filtered": bool(
                from_date or to_date or selected_supplier or status
            ),
            "total_count": SupplierBill.objects.count(),
        },
    )


# ------------------------------------------------------------- production
# Stock made in-house. The only other thing that puts stock on the shelf is a
# supplier bill; everything else takes it off.


class ProductionError(BillError):
    """A reason to roll the save back, worded for the operator.

    Subclasses BillError so the shared _decimal parser's complaints are caught
    here too rather than escaping as a 500.
    """


def _move_stock(product, delta):
    """Apply a signed change to a product's stock.

    Guarded downwards: correcting or removing production takes stock back off
    the shelf, and it may already have been sold. Letting a product hold a
    negative quantity would drop it out of every sales screen, which filters on
    qty > 0.
    """
    if delta >= ZERO:
        Product.objects.filter(pk=product.pk).update(qty=F("qty") + delta)
        return

    needed = -delta
    moved = Product.objects.filter(pk=product.pk, qty__gte=needed).update(
        qty=F("qty") - needed
    )
    if not moved:
        product.refresh_from_db()
        raise ProductionError(
            f"Can't take {needed:.3f} back off {product} — only {product.qty:.3f} "
            f"is left, so some of it has been sold."
        )


@transaction.atomic
def _save_production(payload):
    """Write one day's production, or none of it."""
    production_date = _parse_date(payload.get("production_date"))
    if production_date is None:
        raise ProductionError("Choose a production date.")
    if production_date > timezone.localdate():
        raise ProductionError("Production can't be dated in the future.")

    raw_lines = payload.get("lines")
    if not isinstance(raw_lines, list):
        raise ProductionError(MALFORMED)

    products = {
        product.pk: product
        for product in Product.objects.filter(
            pk__in=[raw.get("product_id") for raw in raw_lines if isinstance(raw, dict)],
            is_active=True,
        )
    }

    entries = []
    seen = set()
    for raw in raw_lines:
        if not isinstance(raw, dict):
            raise ProductionError(MALFORMED)

        qty = _decimal(raw.get("qty_produced"), "Quantity produced", 3)
        # Only rows with something on them are saved; the rest of the table is
        # just the shelf, sitting there at zero.
        if qty == ZERO:
            continue

        product = products.get(raw.get("product_id"))
        if product is None:
            raise ProductionError("A product on this sheet is no longer available.")
        if product.pk in seen:
            raise ProductionError(f"{product} is on the sheet twice.")
        seen.add(product.pk)
        entries.append((product, qty))

    if not entries:
        raise ProductionError("Enter a quantity against at least one product.")

    written = []
    for product, qty in entries:
        # Read inside the transaction: the snapshot has to be the shelf as this
        # entry found it, not as the page rendered it some minutes ago.
        product.refresh_from_db()
        before = product.qty

        written.append(
            ProductionEntry.objects.create(
                product=product,
                production_date=production_date,
                qty_produced=qty,
                stock_before=before,
                stock_after=before + qty,
            )
        )
        _move_stock(product, qty)

    return production_date, written


@transaction.atomic
def _update_production(entry, qty):
    """Correct one entry, moving the shelf by the difference.

    The stored quantity is re-read rather than taken off `entry`: a bound
    ModelForm writes the submitted value onto its instance while validating,
    so by now entry.qty_produced is already the new figure and the difference
    against it would always be zero.
    """
    stored = ProductionEntry.objects.select_related("product").get(pk=entry.pk)
    diff = qty - stored.qty_produced
    _move_stock(stored.product, diff)

    entry.qty_produced = qty
    # stock_before stays: it is what this entry found, and no correction now
    # changes what was on the shelf then. What it left behind does change.
    entry.stock_after = stored.stock_before + qty
    entry.save(update_fields=["qty_produced", "stock_after"])
    return entry


@transaction.atomic
def _delete_production(entry):
    _move_stock(entry.product, -entry.qty_produced)
    entry.delete()


@login_required
def production_create(request):
    if request.method == "POST":
        try:
            payload = json.loads(request.body or b"{}")
        except json.JSONDecodeError:
            return JsonResponse({"success": False, "error": MALFORMED}, status=400)
        if not isinstance(payload, dict):
            return JsonResponse({"success": False, "error": MALFORMED}, status=400)

        try:
            production_date, written = _save_production(payload)
        except BillError as exc:
            return JsonResponse({"success": False, "error": str(exc)}, status=400)

        total = sum((entry.qty_produced for entry in written), ZERO)
        messages.success(
            request,
            f"{len(written)} product{'' if len(written) == 1 else 's'} produced on "
            f"{production_date:%d %b %Y} — {total:.3f} in total.",
        )
        return JsonResponse(
            {"success": True, "redirect": reverse("core:production_list")}
        )

    products = Product.objects.select_related("category")
    return render(
        request,
        "core/production_create.html",
        {
            "products": products.filter(is_active=True),
            "today": timezone.localdate(),
        },
    )


@login_required
def production_edit(request, pk):
    entry = get_object_or_404(
        ProductionEntry.objects.select_related("product"), pk=pk
    )

    form = ProductionEntryForm(request.POST or None, instance=entry)
    if request.method == "POST" and form.is_valid():
        try:
            _update_production(entry, form.cleaned_data["qty_produced"])
        except BillError as exc:
            form.add_error("qty_produced", str(exc))
        else:
            # The stock moved by an F() expression, so the product in memory
            # still holds the figure from before.
            entry.product.refresh_from_db()
            messages.success(
                request,
                f"{entry.product} production on {entry.production_date:%d %b %Y} "
                f"is now {entry.qty_produced:.3f}. Stock is {entry.product.qty:.3f}.",
            )
            return redirect("core:production_list")

    return render(
        request, "core/production_edit.html", {"form": form, "entry": entry}
    )


@require_POST
@super_admin_required
def production_delete(request, pk):
    entry = get_object_or_404(
        ProductionEntry.objects.select_related("product"), pk=pk
    )
    label = f"{entry.product} on {entry.production_date:%d %b %Y}"

    try:
        _delete_production(entry)
    except BillError as exc:
        messages.error(request, f"Cannot delete {label} — {exc}")
        return redirect("core:production_list")

    messages.success(request, f"Production of {label} was deleted and reversed.")
    return redirect("core:production_list")


@login_required
def production_list(request):
    from_date = _parse_date(request.GET.get("from_date"))
    to_date = _parse_date(request.GET.get("to_date"))

    product_id = request.GET.get("product", "").strip()
    selected_product = int(product_id) if product_id.isdigit() else None

    entries = ProductionEntry.objects.select_related("product")
    if from_date:
        entries = entries.filter(production_date__gte=from_date)
    if to_date:
        entries = entries.filter(production_date__lte=to_date)
    if selected_product:
        entries = entries.filter(product_id=selected_product)

    # Newest day first, and within a day the order they were entered.
    entries = entries.order_by("-production_date", "id")

    # Grouped here rather than by a second query per day: the rows are already
    # in hand and already in the right order.
    days = []
    for entry in entries:
        if not days or days[-1]["date"] != entry.production_date:
            days.append(
                {
                    "date": entry.production_date,
                    "entries": [],
                    "total_qty": ZERO,
                }
            )
        days[-1]["entries"].append(entry)
        days[-1]["total_qty"] += entry.qty_produced

    for day in days:
        day["product_count"] = len(day["entries"])

    return render(
        request,
        "core/production_list.html",
        {
            "days": days,
            "products": Product.objects.filter(production_entries__isnull=False).distinct(),
            "from_date": from_date,
            "to_date": to_date,
            "selected_product": selected_product,
            "is_filtered": bool(from_date or to_date or selected_product),
            "entry_count": sum(day["product_count"] for day in days),
            "total_count": ProductionEntry.objects.count(),
        },
    )


@login_required
def ledger_index(request):
    # The sidebar's Customer Ledger entry. The ledger itself is per-customer,
    # at core:customer_ledger — this is still a placeholder section index.
    return render(request, "core/placeholder.html", {"section": "Customer Ledger"})


# -------------------------------------------------------------- sales report
# Read-only. Every figure is derived from bills and their payments, so nothing
# here writes and nothing here should ever disagree with the bill it came from.


def _sales_report_context(request):
    """Everything both the page and the PDF report, from one set of filters."""
    from_date = _parse_date(request.GET.get("from_date"))
    to_date = _parse_date(request.GET.get("to_date"))

    customer_id = request.GET.get("customer_id", "").strip()
    selected_customer = int(customer_id) if customer_id.isdigit() else None

    payment_type = request.GET.get("payment_type", "").strip()
    if payment_type not in {value for value, _ in Bill.PaymentType.choices}:
        payment_type = ""

    # A cancelled bill is not a sale. Leaving them in would overstate every
    # card on the page.
    bills = Bill.objects.select_related("customer").exclude(
        status=Bill.Status.CANCELLED
    )
    if from_date:
        bills = bills.filter(bill_date__gte=from_date)
    if to_date:
        bills = bills.filter(bill_date__lte=to_date)
    if selected_customer:
        bills = bills.filter(customer_id=selected_customer)
    if payment_type:
        bills = bills.filter(payment_type=payment_type)

    bills = bills.annotate(
        # paid_amount can run past total_amount when a payment also clears old
        # debt, and a bill can't owe less than nothing.
        outstanding=Greatest(
            F("total_amount") - F("paid_amount"), Value(ZERO), output_field=MONEY
        )
    ).order_by("bill_date", "id")

    bills = list(bills)
    bill_ids = [bill.pk for bill in bills]

    totals = Bill.objects.filter(pk__in=bill_ids).aggregate(
        sales=Coalesce(Sum("total_amount"), ZERO, output_field=MONEY),
    )
    total_outstanding = sum((bill.outstanding for bill in bills), ZERO)

    # Payments are counted through their bill, so the same date range and the
    # same filters apply to both without asking twice.
    payments = Payment.objects.filter(bill_id__in=bill_ids).select_related(
        "bill", "bill__customer"
    )

    cash_rows = list(
        payments.filter(method=Payment.Method.CASH).order_by("bill__bill_date", "id")
    )
    cheque_rows = list(
        Cheque.objects.filter(payment__bill_id__in=bill_ids)
        .select_related("payment", "payment__bill", "customer")
        .order_by("payment__bill__bill_date", "id")
    )

    total_cash = sum((row.amount for row in cash_rows), ZERO)
    total_cheque = sum((row.amount for row in cheque_rows), ZERO)

    # Cash either stayed in the drawer or was banked; Payment.account says
    # which, and blank means it stayed.
    account_labels = dict(Payment.Account.choices)
    by_account = {"": ZERO}
    for value, _ in Payment.Account.choices:
        by_account[value] = ZERO
    for row in cash_rows:
        by_account[row.account] = by_account.get(row.account, ZERO) + row.amount

    account_totals = [("Physical", by_account.get("", ZERO))]
    for value, label in Payment.Account.choices:
        account_totals.append((f"{label} Acc", by_account.get(value, ZERO)))

    return {
        "bills": bills,
        "cash_rows": cash_rows,
        "cheque_rows": cheque_rows,
        "account_labels": account_labels,
        "account_totals": account_totals,
        "total_sales": totals["sales"],
        "total_cash": total_cash,
        "total_cheque": total_cheque,
        "total_outstanding": total_outstanding,
        "from_date": from_date,
        "to_date": to_date,
        "selected_customer": selected_customer,
        "payment_type": payment_type,
        "customers": Customer.objects.filter(is_supplier=False),
        "payment_types": Bill.PaymentType.choices,
        "is_filtered": bool(
            from_date or to_date or selected_customer or payment_type
        ),
        "generated_at": timezone.localtime(),
    }


@login_required
def sales_report(request):
    context = _sales_report_context(request)
    # The PDF link carries the same filters, so it reports what is on screen.
    context["query"] = request.GET.urlencode()
    return render(request, "core/sales_report.html", context)


@login_required
def sales_report_pdf(request):
    context = _sales_report_context(request)
    html = render_to_string("core/sales_report_pdf.html", context, request=request)

    try:
        from weasyprint import HTML
    except (ImportError, OSError):
        # OSError, not just ImportError: `pip install weasyprint` succeeds on
        # Windows, then importing it raises OSError because the GTK libraries
        # it binds to aren't there. Rather than 500, hand back the very
        # document the PDF is rendered from and let the browser print it.
        messages.warning(
            request,
            "WeasyPrint can't run here, so this is the print view rather than a "
            "PDF download — use your browser's Print to PDF. To get real PDFs, "
            "install WeasyPrint's GTK libraries on the server.",
        )
        return HttpResponse(html)

    pdf = HTML(string=html, base_url=request.build_absolute_uri()).write_pdf()
    response = HttpResponse(pdf, content_type="application/pdf")
    stamp = timezone.localdate().isoformat()
    response["Content-Disposition"] = f'inline; filename="senovka-sales-{stamp}.pdf"'
    return response
