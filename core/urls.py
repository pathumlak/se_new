from django.urls import path

from . import views

app_name = "core"

urlpatterns = [
    # Public landing page. Signed-in users bypass it via a redirect inside the
    # view — bookmarking "/" and staying signed in should land you where you
    # actually work, not on marketing copy you already know.
    path("", views.landing, name="landing"),
    path("dashboard/", views.dashboard, name="dashboard"),
    # Fire-and-forget dismiss for a single notification card. POST-only so a
    # browser back button never re-fires the dismissal.
    path(
        "notifications/dismiss/",
        views.notification_dismiss,
        name="notification_dismiss",
    ),
    # Super-admin only, enforced per view. There is no self-registration.
    path("users/", views.user_list, name="user_list"),
    path("users/create/", views.user_create, name="user_create"),
    path("users/<int:pk>/edit/", views.user_edit, name="user_edit"),
    # Super admin types a new password directly (replacing the older
    # generate-and-show flow).
    path(
        "users/<int:pk>/set-password/",
        views.user_set_password,
        name="user_set_password",
    ),
    path("users/<int:pk>/deactivate/", views.user_deactivate, name="user_deactivate"),
    path("users/<int:pk>/activate/", views.user_activate, name="user_activate"),
    path("users/<int:pk>/delete/", views.user_delete, name="user_delete"),
    # Self-service: every signed-in user has a profile page.
    path("profile/", views.profile, name="profile"),
    path("categories/", views.category_list, name="category_list"),
    path("categories/create/", views.category_create, name="category_create"),
    path("categories/<int:pk>/edit/", views.category_update, name="category_update"),
    path("categories/<int:pk>/delete/", views.category_delete, name="category_delete"),
    path("products/", views.product_list, name="product_list"),
    path(
        "products/export/excel/",
        views.product_export_excel,
        name="product_export_excel",
    ),
    path("products/create/", views.product_create, name="product_create"),
    path("products/<int:pk>/edit/", views.product_update, name="product_update"),
    path("products/<int:pk>/delete/", views.product_delete, name="product_delete"),
    path(
        "products/<int:pk>/toggle-active/",
        views.product_toggle_active,
        name="product_toggle_active",
    ),
    path("products/<int:pk>/prices/", views.product_prices, name="product_prices"),
    # Every stock movement on one product, oldest first, with a running balance
    # and a running production total.
    path(
        "products/<int:pk>/stock-ledger/",
        views.stock_ledger,
        name="stock_ledger",
    ),
    path(
        "products/<int:pk>/stock-ledger/excel/",
        views.stock_ledger_excel,
        name="stock_ledger_excel",
    ),
    # Manual per-product stock corrections. Anyone can add; only a super
    # admin can rewind one.
    path(
        "products/<int:pk>/adjust-stock/",
        views.stock_adjust_create,
        name="stock_adjust_create",
    ),
    path(
        "stock-adjustments/<int:pk>/delete/",
        views.stock_adjust_delete,
        name="stock_adjust_delete",
    ),
    path("customers/", views.customer_list, name="customer_list"),
    path("customers/excel/", views.customer_list_excel, name="customer_list_excel"),
    path("customers/contacts/", views.customer_contacts, name="customer_contacts"),
    path("customers/create/", views.customer_create, name="customer_create"),
    path("customers/<int:pk>/", views.customer_detail, name="customer_detail"),
    path("customers/<int:pk>/edit/", views.customer_update, name="customer_update"),
    path("customers/<int:pk>/delete/", views.customer_delete, name="customer_delete"),
    path("customers/<int:pk>/prices/", views.customer_prices, name="customer_prices"),
    path("customers/<int:pk>/ledger/", views.customer_ledger, name="customer_ledger"),
    # One lump payment fanned out across the customer's outstanding bills,
    # oldest first — see _allocate_settlement.
    path(
        "customers/<int:pk>/settle/",
        views.customer_settle,
        name="customer_settle",
    ),
    # Super-admin only, enforced per view.
    path(
        "customers/<int:pk>/adjustments/create/",
        views.customer_adjustment_create,
        name="customer_adjustment_create",
    ),
    path(
        "customers/<int:pk>/adjustments/<int:adjustment_pk>/edit/",
        views.customer_adjustment_edit,
        name="customer_adjustment_edit",
    ),
    path(
        "customers/<int:pk>/adjustments/<int:adjustment_pk>/delete/",
        views.customer_adjustment_delete,
        name="customer_adjustment_delete",
    ),
    # Serves the Save All button on both price pages above.
    path(
        "api/customer-price/save-all/",
        views.customer_price_save_all,
        name="customer_price_save_all",
    ),
    path("bills/create/", views.bill_create, name="bill_create"),
    # Feeds the step 1 product table on the page above.
    path(
        "api/bill/products/<int:customer_id>/",
        views.bill_products,
        name="bill_products",
    ),
    path("bills/save/", views.bill_save, name="bill_save"),
    # Park a bill mid-entry. GET the list, POST to hold the current form,
    # GET the recall page to hydrate bill_create, POST to drop a stale draft.
    path("bills/held/", views.held_bill_list, name="held_bill_list"),
    path("bills/hold/", views.held_bill_save, name="held_bill_save"),
    path("bills/held/<int:pk>/", views.held_bill_recall, name="held_bill_recall"),
    path(
        "bills/held/<int:pk>/delete/",
        views.held_bill_delete,
        name="held_bill_delete",
    ),
    path("bills/", views.bill_list, name="bill_list"),
    path("bills/excel/", views.bill_list_excel, name="bill_list_excel"),
    path("bills/<int:pk>/", views.bill_detail, name="bill_detail"),
    # GET renders the form; POST rewrites the bill.
    path("bills/<int:pk>/edit/", views.bill_edit, name="bill_edit"),
    path("bills/<int:pk>/delete/", views.bill_delete, name="bill_delete"),
    # Record a follow-up payment against a bill that still owes money.
    path("bills/<int:pk>/pay/", views.bill_add_payment, name="bill_add_payment"),
    path("cheques/", views.cheque_list, name="cheque_list"),
    path("cheques/excel/", views.cheque_list_excel, name="cheque_list_excel"),
    path("cheques/<int:pk>/deposit/", views.cheque_deposit, name="cheque_deposit"),
    path("cheques/<int:pk>/hold/", views.cheque_hold, name="cheque_hold"),
    path("cheques/<int:pk>/bounce/", views.cheque_bounce, name="cheque_bounce"),
    path("cheques/<int:pk>/edit/", views.cheque_edit, name="cheque_edit"),
    path("cheques/<int:pk>/delete/", views.cheque_delete, name="cheque_delete"),
    path("cash-drawer/", views.cash_drawer, name="cash_drawer"),
    path("cash-drawer/excel/", views.cash_drawer_excel, name="cash_drawer_excel"),
    # Manual top-up of the drawer (owner deposit, petty-cash return, etc.).
    path("cash-drawer/insert/", views.cash_drawer_insert, name="cash_drawer_insert"),
    # Manual entries only — both views refuse a bill-linked row. The form is a
    # modal on the list, so edit is POST-only and a GET bounces back to it.
    path("cash-drawer/<int:pk>/edit/", views.cash_drawer_edit, name="cash_drawer_edit"),
    path(
        "cash-drawer/<int:pk>/delete/",
        views.cash_drawer_delete,
        name="cash_drawer_delete",
    ),
    path("supplier-bills/", views.supplier_bill_list, name="supplier_bill_list"),
    # GET renders the form; POST saves it.
    path(
        "supplier-bills/create/",
        views.supplier_bill_create,
        name="supplier_bill_create",
    ),
    path(
        "supplier-bills/<int:pk>/",
        views.supplier_bill_detail,
        name="supplier_bill_detail",
    ),
    path(
        "supplier-bills/<int:pk>/edit/",
        views.supplier_bill_edit,
        name="supplier_bill_edit",
    ),
    path(
        "supplier-bills/<int:pk>/delete/",
        views.supplier_bill_delete,
        name="supplier_bill_delete",
    ),
    # Inline creation from the supplier bill form.
    path("api/supplier/create/", views.supplier_quick_create, name="supplier_quick_create"),
    path("api/product/create/", views.product_quick_create, name="product_quick_create"),
    path("production/", views.production_list, name="production_list"),
    # GET renders the day's sheet; POST saves it.
    path("production/create/", views.production_create, name="production_create"),
    path("production/<int:pk>/edit/", views.production_edit, name="production_edit"),
    path("production/<int:pk>/delete/", views.production_delete, name="production_delete"),
    # Section index only. The ledger itself is per-customer, above.
    path("ledger/", views.ledger_index, name="ledger_index"),
    path("reports/sales/", views.sales_report, name="sales_report"),
    # Same filters as the page above; renders the print template to PDF.
    path("reports/sales/pdf/", views.sales_report_pdf, name="sales_report_pdf"),
    path(
        "reports/ledger/<int:pk>/pdf/",
        views.customer_ledger_pdf,
        name="customer_ledger_pdf",
    ),
    path("reports/outstanding/", views.outstanding_report, name="outstanding_report"),
    path(
        "reports/outstanding/pdf/",
        views.outstanding_report_pdf,
        name="outstanding_report_pdf",
    ),

    # ---- Petty cash. One fund per month, auto-carried; two independent
    # movement types (expenses / reimbursements). All writes are POST-only
    # so a browser back-button never re-fires them.
    path("petty-cash/", views.petty_cash, name="petty_cash"),
    path(
        "petty-cash/expenses/create/",
        views.petty_cash_expense_create,
        name="petty_cash_expense_create",
    ),
    path(
        "petty-cash/expenses/<int:pk>/edit/",
        views.petty_cash_expense_edit,
        name="petty_cash_expense_edit",
    ),
    path(
        "petty-cash/expenses/<int:pk>/delete/",
        views.petty_cash_expense_delete,
        name="petty_cash_expense_delete",
    ),
    path(
        "petty-cash/reimbursements/create/",
        views.petty_cash_reimbursement_create,
        name="petty_cash_reimbursement_create",
    ),
    path(
        "petty-cash/reimbursements/<int:pk>/edit/",
        views.petty_cash_reimbursement_edit,
        name="petty_cash_reimbursement_edit",
    ),
    path(
        "petty-cash/reimbursements/<int:pk>/delete/",
        views.petty_cash_reimbursement_delete,
        name="petty_cash_reimbursement_delete",
    ),
    path("petty-cash/pdf/", views.petty_cash_pdf, name="petty_cash_pdf"),
    path("petty-cash/excel/", views.petty_cash_excel, name="petty_cash_excel"),

    # ---- Material master data. Super-admin only, enforced per view.
    path(
        "material-suppliers/",
        views.material_supplier_list,
        name="material_supplier_list",
    ),
    path(
        "material-suppliers/create/",
        views.material_supplier_create,
        name="material_supplier_create",
    ),
    path(
        "material-suppliers/<int:pk>/edit/",
        views.material_supplier_edit,
        name="material_supplier_edit",
    ),
    path(
        "material-suppliers/<int:pk>/delete/",
        views.material_supplier_delete,
        name="material_supplier_delete",
    ),
    path("materials/", views.material_list, name="material_list"),
    path("materials/create/", views.material_create, name="material_create"),
    path("materials/<int:pk>/edit/", views.material_edit, name="material_edit"),
    path(
        "materials/<int:pk>/delete/",
        views.material_delete,
        name="material_delete",
    ),

    # ---- Material purchases. The main flow. Delete is super-admin only.
    path(
        "material-purchases/",
        views.material_purchase_list,
        name="material_purchase_list",
    ),
    path(
        "material-purchases/create/",
        views.material_purchase_create,
        name="material_purchase_create",
    ),
    path(
        "material-purchases/<int:pk>/",
        views.material_purchase_detail,
        name="material_purchase_detail",
    ),
    path(
        "material-purchases/<int:pk>/edit/",
        views.material_purchase_edit,
        name="material_purchase_edit",
    ),
    path(
        "material-purchases/<int:pk>/delete/",
        views.material_purchase_delete,
        name="material_purchase_delete",
    ),
    path(
        "material-purchases/items/<int:item_pk>/weigh/",
        views.material_purchase_weigh_add,
        name="material_purchase_weigh_add",
    ),
    path(
        "material-purchases/weigh/<int:pk>/edit/",
        views.material_purchase_weigh_edit,
        name="material_purchase_weigh_edit",
    ),
    path(
        "material-purchases/weigh/<int:pk>/delete/",
        views.material_purchase_weigh_delete,
        name="material_purchase_weigh_delete",
    ),

    # ---- Vehicle tracker. Vehicle & Rider CRUD are super-admin; trip
    # CRUD is open to any signed-in user.
    path("vehicles/", views.vehicle_list, name="vehicle_list"),
    path("vehicles/create/", views.vehicle_create, name="vehicle_create"),
    path("vehicles/<int:pk>/edit/", views.vehicle_edit, name="vehicle_edit"),
    path("vehicles/<int:pk>/delete/", views.vehicle_delete, name="vehicle_delete"),
    path("riders/", views.rider_list, name="rider_list"),
    path("riders/create/", views.rider_create, name="rider_create"),
    path("riders/<int:pk>/edit/", views.rider_edit, name="rider_edit"),
    path("riders/<int:pk>/delete/", views.rider_delete, name="rider_delete"),
    path("vehicle-trips/", views.vehicle_trip_list, name="vehicle_trip_list"),
    path("vehicle-trips/create/", views.vehicle_trip_create, name="vehicle_trip_create"),
    path("vehicle-trips/<int:pk>/edit/", views.vehicle_trip_edit, name="vehicle_trip_edit"),
    path("vehicle-trips/<int:pk>/delete/", views.vehicle_trip_delete, name="vehicle_trip_delete"),
    path("vehicle-trips/pdf/", views.vehicle_trip_pdf, name="vehicle_trip_pdf"),

    # ---- Order book (quotations). Nothing moves stock or money — see the
    # Order model docstring.
    path("orders/", views.order_list, name="order_list"),
    path("orders/create/", views.order_create, name="order_create"),
    # Aggregated per-product view: what's been ordered vs what's in stock.
    path(
        "orders/production-check/",
        views.order_production_check,
        name="order_production_check",
    ),
    path("orders/<int:pk>/", views.order_detail, name="order_detail"),
    path("orders/<int:pk>/edit/", views.order_edit, name="order_edit"),
    path("orders/<int:pk>/delete/", views.order_delete, name="order_delete"),
    path(
        "orders/<int:pk>/status/<str:status>/",
        views.order_set_status,
        name="order_set_status",
    ),
    path("orders/<int:pk>/pdf/", views.order_pdf, name="order_pdf"),
    path("orders/<int:pk>/excel/", views.order_excel, name="order_excel"),
    path("orders/<int:pk>/delivery-note/", views.order_delivery_note_excel, name="order_delivery_note"),

    # ---- Daily running machine log. Machine master is super-admin;
    # the daily log itself is open to any signed-in user.
    path("machines/", views.machine_list, name="machine_list"),
    path("machines/create/", views.machine_create, name="machine_create"),
    path("machines/<int:pk>/edit/", views.machine_edit, name="machine_edit"),
    path("machines/<int:pk>/delete/", views.machine_delete, name="machine_delete"),
    path("daily-run/", views.daily_run, name="daily_run"),
    path("daily-run/history/", views.daily_run_history, name="daily_run_history"),
]
