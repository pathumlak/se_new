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
    path("customers/", views.customer_list, name="customer_list"),
    path("bills/new/", views.make_bill, name="make_bill"),
    path("bills/", views.bill_list, name="bill_list"),
    path("cheques/", views.cheque_list, name="cheque_list"),
    path("cash-drawer/", views.cash_drawer, name="cash_drawer"),
    path("supplier-bills/", views.supplier_bill_list, name="supplier_bill_list"),
    path("production/", views.production, name="production"),
    path("ledger/", views.customer_ledger, name="customer_ledger"),
    path("reports/sales/", views.sales_report, name="sales_report"),
]
