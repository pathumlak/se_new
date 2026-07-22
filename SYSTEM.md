# Senovka ERP — System Reference

Single-file summary of the whole Django project. Purpose: give an assistant enough context to answer questions or plan edits without re-reading every source file. Whenever the code diverges from this file, the code wins — re-check before acting on anything load-bearing.

---

## 1. Stack

- **Framework**: Django >= 5.0 (single project `senovka_erp`, single app `core`).
- **Python**: standard `manage.py` entry point.
- **DB**: SQLite (`db.sqlite3` at repo root). Configured in [senovka_erp/settings.py:72](senovka_erp/settings.py:72).
- **Forms/UI**: `django-crispy-forms` with `crispy-tailwind` (Tailwind CSS classes).
- **PDF**: `weasyprint` (used for sales, ledger, outstanding, order, petty-cash, vehicle-trip PDFs). Needs GTK/Pango natively.
- **Excel**: `openpyxl` (product list, cheque list, cash drawer, order, delivery-note, petty-cash, stock ledger, customer list, bill list exports).
- **Auth**: Django's built-in `LoginView` / `LogoutView` only. **No registration flow**. Accounts are seeded via `manage.py seed_users` or created by a super-admin in the app.
- **Timezone**: `Asia/Colombo`. `USE_TZ = True`.
- **Static**: `STATIC_URL=static/`, `STATICFILES_DIRS=[BASE_DIR/static]`, `STATIC_ROOT=staticfiles`.
- **Pagination**: `PAGINATE_BY=25` (lists), `PAGINATE_BY_REPORTS=50` (ledgers/reports). Rows-per-page constants.
- **Stock**: `LOW_STOCK_THRESHOLD=10` triggers "low" badges (qty > 0 and <= 10). Zero / negative are separate states.

Prod deployment (see [deploy.md](deploy.md)):
- Server: Ubuntu/Debian VM at `senovkaplastics.cloud` (72.61.174.119).
- Path: `/var/www/senovka_erp` with `venv/`.
- Serves via `systemctl` unit `senovka_erp` (Gunicorn) behind Nginx, socket `/run/senovka/senovka_erp.sock`.
- `deploy.sh`: `git pull` → `pip install` → `migrate` → `collectstatic` → chown www-data → restart Gunicorn → reload Nginx.
- SSL via certbot.

---

## 2. Layout

```
senovka new system/
├── manage.py
├── db.sqlite3
├── requirements.txt
├── deploy.md
├── senovka_erp/          # Django project (settings, root urls, wsgi/asgi)
│   ├── settings.py
│   └── urls.py           # login/, logout/, admin/, include core.urls at /
├── core/                 # The only app
│   ├── models.py         # ~1400 lines, all domain models
│   ├── views.py          # ~8300 lines, function-based views for everything
│   ├── forms.py          # ~2000 lines, 33 form classes
│   ├── urls.py           # Route names under app_name="core"
│   ├── admin.py          # Only User is registered
│   ├── decorators.py     # role_required, super_admin_required
│   ├── utils.py          # MonthFilter, get_month_filter (?month=YYYY-MM|all)
│   ├── context_processors.py  # current_role, is_super_admin in every template
│   ├── templatetags/pagination.py
│   ├── management/commands/
│   │   ├── seed.py         # Demo data + admin/manager users
│   │   ├── seed_users.py   # Real accounts: Dushan (super), Dinusha, Udara (manager)
│   │   └── notify_cheques.py  # Cron: exit 1 if cheques maturing unbanked
│   ├── migrations/       # 0001–0015 (2026)
│   └── tests*.py
├── templates/
│   ├── base.html
│   ├── 403.html
│   ├── registration/login.html
│   ├── partials/pagination.html
│   └── core/…            # ~70 templates, all list/detail/form/pdf pages
└── static/               # CSS/JS/img assets served in dev by staticfiles
```

`SECRET_KEY` in settings.py is placeholder; `DEBUG=True` is committed. Both must change before real deploys. `ALLOWED_HOSTS` already contains prod host + localhost.

---

## 3. Domain model (core/models.py)

Custom user: `AUTH_USER_MODEL = "core.User"`.

### Roles / auth
- **User** (extends `AbstractUser`): adds `role` (`super_admin` | `manager`, default `manager`).
  - `generate_password()`: 8-char password from an ambiguity-free alphabet (no I, l, 1, O, 0), using `secrets`.
  - `full_name` property is safe when name fields blank.

### Catalogue
- **Category**: `name` unique, `description`.
- **Product**: `name`, `size`, `qty` (Decimal 12/3), `category` (PROTECT), `default_price`, `is_active`.

### Trading parties
- **Customer**: `name`, `phone`, `email`, `address`, `credit_limit`, `balance` (signed — debtors run negative), `is_supplier`, `is_active`, `is_walk_in_account` (single holding account flag for walk-in bills).
- **CustomerPrice**: per-(customer, product) `unit_price` override. Unique together.
- **CustomerBalanceAdjustment**: manual balance move without a bill. `Type` = credit (+) / debit (-). Amount always positive, direction in `adjustment_type`. `signed_amount` property maps to Customer.balance sign.

### Sales / billing
- **Bill**: `customer` (nullable for walk-in), `bill_date`, `subtotal`, `delivery_charge`, `discount_amount`, `discount_reason`, `total_amount`, `paid_amount`, `settled_amount` (written-off), `credit_applied` (snapshot of customer credit consumed), `balance_change` (signed — how bill moved customer.balance), `payment_type`, `status`, `notes`, `is_walk_in`, `walk_in_name`, `edit_reason`, `edit_date`.
  - `PaymentType`: `full_cash`, `full_cheque`, `partial` (legacy — do not use for new bills), `partial_cash`, `partial_cheque`, `mixed`, `pay_later`.
  - `Status`: `draft` | `unpaid` | `partial` | `paid` | `cancelled`.
  - `remaining_balance = total_amount - paid_amount - settled_amount`.
  - `amount_to_collect = total_amount - credit_applied` (display only).
- **BillItem**: `bill` CASCADE, `product` PROTECT, `qty`, `unit_price`, `line_total`.
- **BillSettlement**: money written off a bill (not collected). `Method` cash/cheque. `save()` posts once on create: adds to `bill.settled_amount`, `bill.balance_change`, and `customer.balance`. **Never re-posted** on re-save — edit means reverse+create new.
- **BillEditAudit**: one row per bill edit (Bill.edit_reason holds only the latest). Carries no amount.
- **Payment**: money in against a bill (or a customer directly). `Method` cash/cheque/transfer. `Account` senovka/dinusha. Nullable `bill` allows top-ups that sit as credit; nullable `customer` allows detached payments.
- **Cheque**: hangs off a Payment. `Status` pending/deposited/bounced/held. `bill` set when the cheque arrived at settlement time. `bounce_new_date` for re-presentation.
- **CashTransfer**: senovka ↔ dinusha account transfer, tied to a Payment.
- **CashDrawer**: manual drawer entries. `TxnType` in/out/transfer. Optional `bill` link (bill-linked rows are read-only — cannot be edited/deleted via drawer views). Edit metadata: `edit_reason`, `edited_at`, `edited_by`.
- **HeldBill**: parked draft. Stores raw form `payload` as JSON; nothing else moves. On save-for-real the held row is deleted. Fields cached from payload: `label`, `item_count`, `subtotal`.

### Suppliers (finished-goods side)
- **SupplierBill**: `supplier` (a Customer with `is_supplier=True`), `bill_date`, `total_amount`, `paid_amount`, `status`, `notes`.
- **SupplierBillItem**: `product`, `qty`, `unit_price`, `line_total`.

### Petty cash (per month)
- **PettyCashFund**: exactly one row per month (unique). `month` = first-of-month DateField. `opening_balance` snapshotted from previous month's closing. `closing_balance` cached; `recalculate()` rewrites it from all entries + reimbursements.
  - `PettyCashFund.for_month(month)` → `(fund, carried_from)`.
- **PettyCashEntry**: money out of the tin. `Category` food/transport/office/utilities/maintenance/other. `EntryType` expense (default) / reimbursement.
- **PettyCashReimbursement**: money into the tin (float top-up), `given_by` free text.

### Materials (raw purchasing — separate ledger)
- **MaterialSupplier**: deliberately NOT a `Customer(is_supplier=True)` — no balance/credit tracking.
- **Material**: `name`, `unit` (kg/g/l/ml/m/piece), `default_unit_price`, `is_active`.
- **MaterialPurchase**: `supplier`, `purchase_date`, `invoice_no`, `total_amount`, `status` (pending / partially_weighed / fully_weighed), `notes`. `refresh_status()` recomputes status + total from items.
- **MaterialPurchaseItem**: `ordered_qty`, `unit_price`, `line_total`, `weighed_qty` (cached), `is_weighed`. `recalculate_weighed()` re-sums from weigh entries.
- **MaterialWeighEntry**: one trip to the scale — `weigh_date`, `weighed_qty`, `checked_by` (free text), `submitted_by` (User).

### Vehicles
- **Vehicle**: `name`, `registration_no`, `is_active`.
- **Rider**: `name`, `phone`, `is_active`.
- **VehicleTrip**: single leg (km per trip — deliberately not an odometer reading).

### Numbering
- **ReferenceCounter**: monotonic per-`key` counter that survives row deletion. `next_value(key)` uses `select_for_update`. Used by `Order.save()` for `ORD-####`.

### Order book (quotations — nothing moves stock or money)
- **Order**: `customer` (nullable), `customer_name`, `order_date`, `valid_until`, `reference_no` (auto `ORD-0001`), `notes`, `discount_amount`, `discount_reason`, `delivery_charge`, `subtotal`, `total_amount`, `status` (draft/sent/confirmed/cancelled). `REFERENCE_KEY = "order"`. `recalculate()` re-derives totals from items.
- **OrderItem**: `product`, `qty`, `unit_price`, `line_total`.

### Stock / production
- **ProductionEntry**: `product`, `production_date`, `qty_produced`, `reason`, `stock_before`, `stock_after` (snapshot). Bill-save creates `OVERSALE_REASON_PREFIX = "Oversale —"` entries when a sale exceeds stock; `_reverse_bill` finds & deletes those by prefix on edit/delete.
- **StockAdjustment**: manual correction. `qty` **signed** (positive add, negative remove). `stock_before` / `stock_after` snapshots. Distinct from ProductionEntry: production = "we made this", adjustment = "we reconciled to this".

### Machine daily log
- **Machine**: `name` unique, `is_active` (permanent decommission flag), `notes`.
- **DailyMachineRun**: unique `(run_date, machine)`. `Status` running / not_working. Absence of row = "not logged yet"; row with `not_working` = "sat idle". `operator` free text, `product` nullable (blank when not running).
- **DailyOtherWork**: unique `run_date`. Free-text `driver`, `material_supply`, `material_mixing`, `other`.

---

## 4. URLs (core/urls.py, app_name="core")

Prefix `/` mounts `core.urls`; `/login/`, `/logout/`, `/admin/` are project-level.

Root section groupings (all under `core:` namespace):

| Section | URL pattern | Access |
|---|---|---|
| `dashboard` | `/dashboard/` | login |
| Users | `/users/`, `/users/create/`, `/users/<pk>/edit/`, `/users/<pk>/set-password/`, `/users/<pk>/deactivate/`, `/users/<pk>/activate/` | super_admin |
| Profile | `/profile/` | login (self) |
| Categories | `/categories/…` | super_admin |
| Products | `/products/…` + `/products/<pk>/prices/` + `/products/<pk>/stock-ledger/` + `/products/<pk>/adjust-stock/` + `/stock-adjustments/<pk>/delete/` + `/products/export/excel/` + `/products/<pk>/stock-ledger/excel/` | mixed (create/edit super_admin; view login; adjust login; adjust-delete super_admin) |
| Customers | `/customers/…` + `/customers/<pk>/prices/` + `/customers/<pk>/ledger/` + `/customers/<pk>/settle/` + `/customers/<pk>/adjustments/…` + `/customers/contacts/` + `/customers/excel/` | mixed (adjustments super_admin) |
| Bills | `/bills/create/`, `/bills/save/`, `/bills/`, `/bills/<pk>/`, `/bills/<pk>/edit/`, `/bills/<pk>/delete/`, `/bills/<pk>/pay/`, `/bills/excel/` | login (edit/delete may gate) |
| Held bills | `/bills/held/`, `/bills/hold/`, `/bills/held/<pk>/`, `/bills/held/<pk>/delete/` | login |
| Bill helpers (JSON APIs) | `/api/bill/products/<customer_id>/`, `/api/customer-price/save-all/`, `/api/supplier/create/`, `/api/product/create/` | login |
| Cheques | `/cheques/…` (`deposit`/`hold`/`bounce`/`edit`/`delete`) + `/cheques/excel/` | login |
| Cash drawer | `/cash-drawer/`, `/insert/`, `/<pk>/edit/`, `/<pk>/delete/`, `/excel/` | login (bill-linked rows read-only) |
| Supplier bills | `/supplier-bills/…` | login |
| Production | `/production/`, `/production/create/`, `/production/<pk>/edit/`, `/production/<pk>/delete/` | login |
| Ledger index | `/ledger/` | login |
| Reports | `/reports/sales/`, `/reports/sales/pdf/`, `/reports/ledger/<pk>/pdf/`, `/reports/outstanding/`, `/reports/outstanding/pdf/` | login |
| Petty cash | `/petty-cash/`, `.../expenses/(create,edit,delete)`, `.../reimbursements/(…)`, `/petty-cash/pdf/`, `/petty-cash/excel/` | login |
| Material suppliers | `/material-suppliers/…` | super_admin |
| Materials | `/materials/…` | super_admin |
| Material purchases | `/material-purchases/…` + `.../items/<item_pk>/weigh/` + `.../weigh/<pk>/(edit,delete)` | login (delete super_admin) |
| Vehicles / Riders | `/vehicles/…`, `/riders/…` | super_admin |
| Vehicle trips | `/vehicle-trips/…` + `/vehicle-trips/pdf/` | login |
| Orders (quotations) | `/orders/…` + `/orders/production-check/` + `/orders/<pk>/(status,pdf,excel,delivery-note)` | login |
| Machines | `/machines/…` | super_admin |
| Daily run | `/daily-run/`, `/daily-run/history/` | login |

`RedirectView` at `/` sends to `core:dashboard`.

---

## 5. Views (core/views.py) — the map

All function-based. Auth-gated with `@login_required` (baseline) or `@super_admin_required` (writes for master-data sections). Writes are POST-only via `@require_POST` where applicable.

### Private helpers (leading `_`)

**Pagination / misc**
- `_paginate(request, object_list, per_page=None)` — one page via `Paginator.get_page`.
- `_is_super_admin(user)` — role check for views that stay open to managers with reduced form.
- `_warning_signature(today, cheques)` — dashboard cheque-warning dismissal key (day-scoped).
- `_cash_drawer_balance(queryset=None)` — running cash balance.
- `_format_updated(dt)` — display helper.

**User session**
- `_stash_credentials` / `_pop_credentials` — one-shot session slot for newly generated password (legacy; new flow types password directly).

**Stock ledger**
- `_stock_ledger_rows(product)` — the ordered stream of movements per product (bills, production, adjustments) with running balance + running production total.

**Customer**
- `_customers()`, `_billable_customers()`, `_walk_in_customer()` — canonical querysets and the singleton walk-in holding account.
- `_delete_blockers(customer)` — list of PROTECTed rows blocking delete.
- `_apply_adjustment` / `_reverse_adjustment` — move Customer.balance for a `CustomerBalanceAdjustment`.
- `_parse_date`, `_ledger_rows(customer, from_date, to_date)` — customer ledger stream.

**Bill lifecycle** (most delicate area)
- `_qty_text`, `_decimal`, `_optional_decimal`, `_read_bill_date`, `_read_cheque`, `_read_cheques`, `_read_cash_account` — payload parsing.
- `_read_payment(raw, total, customer)`, `_read_walkin_payment(raw, total)` — dispatch by `payment_type`.
- `_check_credit_limit(customer, total, parts, user)` — gate on the credit line.
- `_record_payments(bill, customer, parts, when)` — write Payment/Cheque/CashTransfer rows and post to Bill/Customer.
- `_reverse_bill(bill)` — undo everything a bill did: reverses stock (uses `OVERSALE_REASON_PREFIX` to find auto-created production rows), balance, payments, cheques, cash-drawer rows, settlements.
- `_save_bill`, `_update_bill`, `_write_bill(bill, user, payload)` — top-level create/edit path (transactional).
- `_refresh_bill_status(bill)` — recompute paid/partial/unpaid from figures.
- `_outstanding_bills_for(customer)`.
- `_allocate_settlement(customer, cash, cash_account, cheques, user, when)` — spread a lump payment across outstanding bills, oldest first.
- `_bills_with_counts()`, `_reversal_summary(bill)`, `_bill_initial(bill)`, `_payment_initial(bill)` — list & form prep.
- `_edit_gate_key`, `_read_edit_gate` — reason-required gate for editing a bill.
- `_held_bill_label`, `_held_bill_snapshot` — HeldBill display cache.
- `_filtered_bills(request)` — filters on bill list.

**Cheque**
- `_cheque_credit(status, amount)`, `_move_balance_for_cheque(cheque, was_status, was_amount)`, `_cheque_balance_note(cheque, delta)`, `_set_cheque_status(request, pk, status, bounce_new_date)`.

**Cash drawer**
- `_account_banked(account)`, `_is_manual(entry)`, `_cash_drawer_page(...)`, `_drawer_balance_without(entry)`.

**Supplier bill**
- `_supplier_products`, `_read_supplier_lines`, `_reverse_supplier_bill`, `_write_supplier_bill`, `_save_supplier_bill`, `_update_supplier_bill`, `_supplier_bill_payload`, `_supplier_bill_form_context`.

**Production**
- `_move_stock(product, delta)`, `_save_production`, `_update_production`, `_delete_production`.

**Reports**
- `_sales_report_context`, `_pdf_response(request, template, context, filename)`, `_outstanding_context`.

**Petty cash**
- `_petty_cash_context(request, active_tab)`, `_petty_cash_redirect(request)`.

**Materials**
- `_parse_purchase_items(raw_json)`, `_material_form_context(...)`.

**Vehicle / Trips**
- `_month_km_for(qs, month_filter, group_field, name_field)`, `_vehicle_trip_context`, `_vehicle_trip_redirect`.

**Orders**
- `_order_line_price(customer_id, product)`, `_parse_order_items(raw_json, customer_id)`, `_order_form_context(...)`, `_order_pdf_context(order)`.

**Daily run**
- `_daily_run_date(request)`.

### Public view functions

Roughly one triple per resource (`_list`, `_create`, `_edit`/`_update`, `_delete`, sometimes `_detail`, `_toggle_active`, `_excel`, `_pdf`). See §4 for the URL → view name mapping (they match 1:1).

Notable ones:
- `dashboard` — KPIs (total outstanding, today's sales, cash balance), maturing cheques, recent bills, top debtors, 7-day sales chart, 30-day payment mix.
- `bill_create` + `bill_products` (JSON API for customer→product grid) + `bill_save` — the sales flow.
- `bill_edit` — reason gate + full reverse-and-rewrite (never partial patch).
- `customer_settle` — one lump goes across bills oldest first.
- `cheque_deposit` / `_hold` / `_bounce` — status transitions that also move customer balance via `_move_balance_for_cheque`.
- `stock_ledger` — chronological movements + running balance; `stock_ledger_excel` mirrors it.
- `sales_report` / `sales_report_pdf` — filterable range.
- `outstanding_report` / `outstanding_report_pdf` — debtor snapshot.
- `petty_cash` — tabbed page (expenses / reimbursements) scoped to selected month via `MonthFilter`.
- `order_production_check` — aggregated open-order qty per product vs `Product.qty` on the shelf.
- `daily_run` — one row per (date, machine) with `DailyOtherWork` free-text sidecar.

---

## 6. Forms (core/forms.py) — 33 classes

Grouped by area:
- **User / auth**: `UserCreateForm`, `UserEditForm` (drops role/is_active when `is_self`), `SetUserPasswordForm`, `ProfilePasswordForm`, `ProfileDetailsForm`.
- **Master data**: `CategoryForm`, `ProductForm`, `ProductQuickForm` (inline creation from bill/supplier forms), `CustomerForm`, `SupplierQuickForm`, `MaterialSupplierForm`, `MaterialForm`, `VehicleForm`, `RiderForm`, `MachineForm`.
- **Bills**: `BillEditReasonForm`, `BillPaymentForm`, `CustomerSettlementForm`.
- **Cheques**: `ChequeForm`.
- **Cash drawer**: `CashDrawerOutForm`, `CashDrawerInForm`, `CashDrawerEditForm`.
- **Petty cash**: `PettyCashExpenseForm`, `PettyCashReimbursementForm`.
- **Material purchases**: `MaterialPurchaseHeaderForm`, `MaterialWeighEntryForm`.
- **Vehicle trips**: `VehicleTripForm`.
- **Orders**: `OrderHeaderForm`.
- **Production / stock**: `ProductionEntryForm`, `StockAdjustmentForm`.
- **Prices**: `CustomerPriceForm`.
- **Balance corrections**: `CustomerBalanceAdjustmentForm`.
- **Daily run**: `DailyOtherWorkForm`.

Most extend `forms.ModelForm` and wire Tailwind classes via crispy-tailwind.

---

## 7. Templates

Base: `templates/base.html` (site chrome, nav, messages, current-role guards). `403.html` for forbidden. `registration/login.html` for the login screen.

`templates/core/` has ~70 files: one `_list`, one `_form`, one `_detail`, plus PDF templates for reports and orders. Partials with a leading underscore are included (e.g. `_bill_form.html`, `_bill_create_js.html`, `_cheque_fields.html`, `_price_table_js.html`). `partials/pagination.html` renders the pager rendered via `templatetags/pagination.py`.

---

## 8. Management commands

- `seed_users` — production accounts (Dushan super-admin, Dinusha + Udara manager). Passwords from env vars `SENOVKA_ADMIN_PASSWORD` / `SENOVKA_MANAGER_PASSWORD`, dev fallbacks embedded. Idempotent.
- `seed` — demo data (categories, products, customer prices) + `admin`/`manager` users with `admin123`/`manager123`. Dev only.
- `notify_cheques` — cron job. Lists cheques maturing within `CHEQUE_WARNING_DAYS`; mails admins; exit code 0/1 for wrapper scripts.

---

## 9. Money & ledger invariants

- `Customer.balance` sign: **negative = customer owes us (debtor)**, positive = we owe them (credit).
- A **Bill** sets `balance_change` (signed) at write time; edit/delete reverses it via `_reverse_bill`, so the customer's running balance stays true through any rewrite.
- A **Payment** collects money; a **BillSettlement** writes debt off. Never conflate them — `paid_amount` and `settled_amount` on Bill are separate.
- **Cheque** status transitions can move Customer.balance via `_move_balance_for_cheque` (a bounced cheque undoes the credit that a deposited one gave).
- **Credit applied**: `Bill.credit_applied` is a snapshot for display only; the actual balance move is in `balance_change`.
- **Reference numbers**: never derive next-number from `MAX(id)+1`; use `ReferenceCounter.next_value("order")` — deletes must not free numbers.
- **Petty cash**: `PettyCashFund.closing_balance` is cached; call `recalculate()` after any write. `opening_balance` is a snapshot copied from the previous month at fund creation — never edited afterwards.
- **Material weigh totals**: `MaterialPurchaseItem.weighed_qty` cached; call `recalculate_weighed()` after weigh entry writes.

---

## 10. Auth / roles cheat sheet

- Two roles: `super_admin`, `manager` (defaults to manager). `is_superuser` (Django concept) is separate; `role` is the app-level gate.
- `@super_admin_required` decorator wraps `@login_required` and bounces managers to dashboard with a flash. Defined in `core/decorators.py`.
- `context_processor.current_role` and `is_super_admin` are in every template.
- `LOGIN_URL="login"`, `LOGIN_REDIRECT_URL="core:dashboard"`, `LOGOUT_REDIRECT_URL="login"`.
- **No password reset / registration**. Password changes: super-admin uses `user_set_password`; users use `/profile/`.

---

## 11. Filtering utility

`MonthFilter` (in [core/utils.py](core/utils.py)) is a frozen dataclass. Views call `get_month_filter(request, param="month")` → returns MonthFilter parsed from `?month=YYYY-MM` or `?month=all` (defaults to current month). Then `mf.apply(queryset, field="date")` narrows to that month; other props: `start`, `end` (inclusive), `previous`, `next`, `label` ("July 2026" / "All time"), `param`, `is_all_time`.

Used by petty cash, vehicle trip list, daily run, and any list scoped by month.

---

## 12. Common gotchas / conventions

- Views use `Paginator.get_page` (via `_paginate`), which coerces garbage `?page=` to a real page rather than raising.
- Bill edits **always** go via `_reverse_bill` + `_write_bill` — never patch fields in place. Same for supplier bill, order, production.
- Deletes are `@require_POST`. GETs on delete URLs bounce back to the detail/list page.
- `SET_NULL` on `CashDrawer.bill` so deleting a bill leaves manual-cash-in rows behind but disowns them; `_is_manual(entry)` gates edit/delete on drawer rows to reject bill-linked ones.
- `PROTECT` on Product/Category/Customer/Supplier/Material/Vehicle/Rider so deletes bounce with `ProtectedError` — the delete views translate that to a flash listing blockers.
- Sales report PDF and outstanding PDF need WeasyPrint. If GTK/Pango missing at runtime → the sales report falls back to a print-friendly HTML page (browser Print to PDF).
- Excel exports use `openpyxl` and stream directly with a Response `Content-Disposition` attachment header.
- Decimal precision: money 12/2, quantity 12/3. Everything is Decimal, never float.
- Timezone-naive dates for `bill_date`, `run_date`, etc. Timezone-aware datetimes for `paid_at`, `created_at`, etc. `USE_TZ=True`, so use `timezone.localdate()` for "today".
- Every list page respects `PAGINATE_BY`; ledgers/reports use `PAGINATE_BY_REPORTS`.

---

## 13. When editing this project — quick checks

- **Adding a Bill field**: update `_write_bill`, `_reverse_bill`, `_bill_initial`, both templates (`_bill_form.html`, `bill_detail.html`), and the excel export.
- **Adding a Customer balance mover**: match the pattern in `BillSettlement.save()` — one-shot post on create, idempotent; reversal is a separate write, never re-post on update.
- **New numbered document type**: add a `REFERENCE_KEY` and call `ReferenceCounter.next_value` in `save()`.
- **New month-scoped list page**: call `get_month_filter(request)`, pass the `MonthFilter` into the template as `month_filter`, and use `mf.apply(qs, "…date_field")`.
- **New super-admin section**: decorate with `@super_admin_required` and add a nav guard in `base.html` using `is_super_admin`.
- **Any stock-moving code**: also emit a stock ledger row (either `ProductionEntry` or `StockAdjustment`), never bare `Product.qty += …`. `_move_stock(product, delta)` is the helper.

---

## 14. Do not touch without a data migration

- `OVERSALE_REASON_PREFIX = "Oversale —"` — matched by string prefix in `_reverse_bill`.
- Bill.PaymentType.PARTIAL — legacy value, kept only for old rows.
- `ReferenceCounter` key `"order"` — used by `Order.REFERENCE_KEY`.
- `Customer.is_walk_in_account` — flag, not name lookup; renaming the walk-in account is fine, but changing this field would leave orphan holding accounts.
