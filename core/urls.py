from django.urls import path
from django.views.generic import RedirectView

from . import views

app_name = "core"

urlpatterns = [
    # Bare root sends signed-in users to the dashboard, anonymous ones to login.
    path(
        "",
        RedirectView.as_view(pattern_name="core:dashboard", permanent=False),
        name="home",
    ),
    path("dashboard/", views.dashboard, name="dashboard"),
    path("categories/", views.category_list, name="category_list"),
    path("categories/create/", views.category_create, name="category_create"),
    path("categories/<int:pk>/edit/", views.category_update, name="category_update"),
    path("categories/<int:pk>/delete/", views.category_delete, name="category_delete"),
    path("products/", views.product_list, name="product_list"),
    path("products/create/", views.product_create, name="product_create"),
    path("products/<int:pk>/edit/", views.product_update, name="product_update"),
    path("products/<int:pk>/delete/", views.product_delete, name="product_delete"),
    path(
        "products/<int:pk>/toggle-active/",
        views.product_toggle_active,
        name="product_toggle_active",
    ),
    path("products/<int:pk>/prices/", views.product_prices, name="product_prices"),
    path("customers/", views.customer_list, name="customer_list"),
    path("customers/create/", views.customer_create, name="customer_create"),
    path("customers/<int:pk>/", views.customer_detail, name="customer_detail"),
    path("customers/<int:pk>/edit/", views.customer_update, name="customer_update"),
    path("customers/<int:pk>/delete/", views.customer_delete, name="customer_delete"),
    path("customers/<int:pk>/prices/", views.customer_prices, name="customer_prices"),
    path("customers/<int:pk>/ledger/", views.customer_ledger, name="customer_ledger"),
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
    path("bills/", views.bill_list, name="bill_list"),
    path("bills/<int:pk>/", views.bill_detail, name="bill_detail"),
    # GET renders the form; POST rewrites the bill.
    path("bills/<int:pk>/edit/", views.bill_edit, name="bill_edit"),
    path("bills/<int:pk>/delete/", views.bill_delete, name="bill_delete"),
    path("cheques/", views.cheque_list, name="cheque_list"),
    path("cheques/<int:pk>/deposit/", views.cheque_deposit, name="cheque_deposit"),
    path("cheques/<int:pk>/hold/", views.cheque_hold, name="cheque_hold"),
    path("cheques/<int:pk>/bounce/", views.cheque_bounce, name="cheque_bounce"),
    path("cheques/<int:pk>/edit/", views.cheque_edit, name="cheque_edit"),
    path("cheques/<int:pk>/delete/", views.cheque_delete, name="cheque_delete"),
    path("cash-drawer/", views.cash_drawer, name="cash_drawer"),
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
]
