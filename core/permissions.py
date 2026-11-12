"""The backend source of truth for manager-facing ERP capabilities."""

PERMISSION_REGISTRY = [
    ("Dashboard", "dashboard.view", "View dashboard", "View", False),
    ("Products", "products.view", "View products", "View", False),
    ("Products", "products.create", "Create products", "Create", False),
    ("Products", "products.edit", "Edit products", "Edit", True),
    ("Products", "products.delete", "Delete products", "Delete", True),
    ("Product Pricing", "pricing.view", "View product prices", "View", False),
    ("Product Pricing", "pricing.create", "Create products with pricing", "Create", True),
    ("Product Pricing", "pricing.edit", "Edit product prices", "Edit", True),
    ("Product Pricing", "pricing.bulk_edit", "Bulk update customer prices", "Edit", True),
    ("Categories", "categories.view", "View categories", "View", False),
    ("Categories", "categories.manage", "Manage categories", "Manage", True),
    ("Inventory and Stock", "stock.view", "View stock and ledgers", "View", False),
    ("Inventory and Stock", "stock.create", "Create stock adjustments", "Create", True),
    ("Inventory and Stock", "stock.edit", "Edit stock adjustments", "Edit", True),
    ("Inventory and Stock", "stock.delete", "Delete stock adjustments", "Delete", True),
    ("Customer Management", "customers.view", "View customers", "View", False),
    ("Customer Management", "customers.create", "Create customers", "Create", False),
    ("Customer Management", "customers.edit", "Edit customers", "Edit", False),
    ("Customer Management", "customers.delete", "Delete customers", "Delete", True),
    ("Customer Balances", "balances.view", "View customer ledgers", "View", False),
    ("Customer Balances", "balances.use", "Use existing balance in billing", "Manage", True),
    ("Customer Balances", "balances.set", "Set a new balance during billing", "Manage", True),
    ("Customer Balances", "balances.edit", "Modify customer balances", "Edit", True),
    ("Customer Balances", "balances.opening_edit", "Edit opening balances", "Edit", True),
    ("Customer Payments", "payments.create", "Record customer payments", "Create", True),
    ("Customer Payments", "payments.edit", "Edit payment records", "Edit", True),
    ("Customer Payments", "payments.delete", "Delete payment records", "Delete", True),
    ("Sales and Billing", "billing.view", "View bills", "View", False),
    ("Sales and Billing", "billing.create", "Create bills", "Create", True),
    ("Sales and Billing", "billing.edit", "Edit bills", "Edit", True),
    ("Sales and Billing", "billing.delete", "Delete bills", "Delete", True),
    ("Sales and Billing", "billing.settle", "Settle bills", "Approve", True),
    ("Sales and Billing", "billing.partial_payments", "Manage partial payments", "Manage", True),
    ("Customer Returns", "returns.view", "View customer returns", "View", False),
    ("Customer Returns", "returns.manage", "Manage customer returns", "Manage", True),
    ("Suppliers", "suppliers.manage", "Manage suppliers", "Manage", True),
    ("Supplier Bills", "supplier_bills.view", "View supplier bills", "View", False),
    ("Supplier Bills", "supplier_bills.manage", "Manage supplier bills", "Manage", True),
    ("Raw Material Purchasing", "purchasing.manage", "Manage raw material purchasing", "Manage", True),
    ("Production", "production.view", "View production", "View", False),
    ("Production", "production.manage", "Manage production", "Manage", True),
    ("Petty Cash and Expenses", "petty_cash.view", "View petty cash", "View", False),
    ("Petty Cash and Expenses", "petty_cash.manage", "Manage petty cash", "Manage", True),
    ("Cash Drawer", "cash_drawer.view", "View cash drawer", "View", False),
    ("Cash Drawer", "cash_drawer.manage", "Manage cash drawer", "Manage", True),
    ("Cheques", "cheques.view", "View cheques", "View", False),
    ("Cheques", "cheques.manage", "Manage cheques", "Manage", True),
    ("Reports and Exports", "reports.view", "View reports", "View", False),
    ("Reports and Exports", "reports.export", "Export reports", "Export", False),
    ("Orders and Quotations", "orders.view", "View orders", "View", False),
    ("Orders and Quotations", "orders.manage", "Manage orders", "Manage", True),
    ("Vehicles", "vehicles.manage", "Manage vehicles", "Manage", True),
    ("Riders and Trips", "trips.manage", "Manage riders and trips", "Manage", True),
    ("Billing Settings", "billing_settings.manage", "Manage billing settings", "Manage", True),
    ("Audit Logs", "audit.view", "View audit logs", "View", True),
    ("Database Administration", "database.manage", "Use database administration", "Manage", True),
    ("User Management", "user_management.view", "View users", "View", True),
    ("User Management", "user_management.manage", "Manage users", "Manage", True),
]


# Every concrete ERP table is also exposed as a CRUD capability. These are
# intentionally separate from the business-operation permissions above: a
# manager may need to edit a product record without receiving permission to
# alter a bill or its accounting side effects.
MODEL_PERMISSION_SPECS = [
    ("Category", "Categories"), ("Product", "Products"), ("Customer", "Customers"),
    ("CustomerBalanceAdjustment", "Customer Balances"),
    ("CustomerOpeningBalanceEditAudit", "Customer Balances"),
    ("CustomerPrice", "Product Pricing"), ("Bill", "Sales and Billing"),
    ("BillItem", "Sales and Billing"), ("BillSettlement", "Sales and Billing"),
    ("BillEditAudit", "Sales and Billing"), ("Payment", "Customer Payments"),
    ("PaymentEditAudit", "Customer Payments"), ("Cheque", "Cheques"),
    ("ChequeReceivedDateEditAudit", "Cheques"), ("CashTransfer", "Cash Drawer"),
    ("CashDrawer", "Cash Drawer"), ("SupplierBill", "Supplier Bills"),
    ("SupplierBillEditAudit", "Supplier Bills"), ("SupplierBillItem", "Supplier Bills"),
    ("HeldBill", "Sales and Billing"), ("PettyCashFund", "Petty Cash and Expenses"),
    ("PettyCashEntry", "Petty Cash and Expenses"),
    ("PettyCashReimbursement", "Petty Cash and Expenses"),
    ("MaterialSupplier", "Suppliers"), ("Material", "Raw Material Purchasing"),
    ("MaterialPurchase", "Raw Material Purchasing"),
    ("MaterialPurchaseItem", "Raw Material Purchasing"),
    ("MaterialWeighEntry", "Raw Material Purchasing"), ("Vehicle", "Vehicles"),
    ("Rider", "Riders and Trips"), ("VehicleTrip", "Riders and Trips"),
    ("Order", "Orders and Quotations"), ("OrderItem", "Orders and Quotations"),
    ("ProductionEntry", "Production"), ("OversaleRecord", "Inventory and Stock"),
    ("StockAdjustment", "Inventory and Stock"), ("Machine", "Production"),
    ("DailyMachineRun", "Production"), ("DailyOtherWork", "Production"),
    ("BillingSettings", "Billing Settings"), ("AuditLog", "Audit Logs"),
    ("CustomerReturn", "Customer Returns"), ("CustomerReturnItem", "Customer Returns"),
]

for model_name, module in MODEL_PERMISSION_SPECS:
    label = model_name.replace("_", " ")
    for operation, title in (
        ("view", "View"), ("create", "Create"), ("edit", "Edit"), ("delete", "Delete")
    ):
        PERMISSION_REGISTRY.append(
            (
                module,
                f"data.{model_name.lower()}.{operation}",
                f"{title} {label}",
                title,
                operation != "view",
            )
        )


PERMISSION_ALIASES = {
    "products.view": ("data.product.view",),
    "products.create": ("data.product.create",),
    "products.edit": ("data.product.edit",),
    "products.delete": ("data.product.delete",),
    "pricing.view": ("data.productprice.view",),
    "pricing.create": ("data.product.create",),
    "pricing.edit": ("data.product.edit", "data.customerprice.edit"),
    "pricing.bulk_edit": ("data.customerprice.edit",),
    "categories.view": ("data.category.view",),
    "categories.manage": ("data.category.create", "data.category.edit", "data.category.delete"),
    "customers.view": ("data.customer.view",),
    "customers.create": ("data.customer.create",),
    "customers.edit": ("data.customer.edit",),
    "customers.delete": ("data.customer.delete",),
    "balances.view": ("data.customer.view",),
    "balances.edit": ("data.customerbalanceadjustment.create", "data.customerbalanceadjustment.edit"),
    "billing.view": ("data.bill.view",),
    "billing.create": ("data.bill.create",),
    "billing.edit": ("data.bill.edit",),
    "billing.delete": ("data.bill.delete",),
    "payments.create": ("data.payment.create",),
    "payments.edit": ("data.payment.edit",),
    "payments.delete": ("data.payment.delete",),
    "cheques.view": ("data.cheque.view",),
    "cheques.manage": ("data.cheque.create", "data.cheque.edit", "data.cheque.delete"),
    "supplier_bills.view": ("data.supplierbill.view",),
    "supplier_bills.manage": ("data.supplierbill.create", "data.supplierbill.edit", "data.supplierbill.delete"),
    "production.view": ("data.productionentry.view",),
    "production.manage": ("data.productionentry.create", "data.productionentry.edit", "data.productionentry.delete"),
    "petty_cash.view": ("data.pettycashentry.view",),
    "petty_cash.manage": ("data.pettycashentry.create", "data.pettycashentry.edit", "data.pettycashentry.delete"),
    "cash_drawer.view": ("data.cashdrawer.view",),
    "cash_drawer.manage": ("data.cashdrawer.create", "data.cashdrawer.edit", "data.cashdrawer.delete"),
    "orders.view": ("data.order.view",),
    "orders.manage": ("data.order.create", "data.order.edit", "data.order.delete"),
    "returns.view": ("data.customerreturn.view",),
    "returns.manage": ("data.customerreturn.create", "data.customerreturn.edit", "data.customerreturn.delete"),
    "billing_settings.manage": ("data.billingsettings.view", "data.billingsettings.edit"),
    "audit.view": ("data.auditlog.view",),
}


URL_PERMISSION_MAP = {
    "dashboard": "dashboard.view",
    "product_list": "products.view", "product_create": "products.create",
    "product_update": "products.edit", "product_delete": "products.delete",
    "product_toggle_active": "products.edit", "product_export_excel": "products.view",
    "product_prices": "pricing.view", "customer_prices": "pricing.view",
    "customer_price_save_all": "pricing.bulk_edit", "product_quick_create": "pricing.create",
    "category_list": "categories.view", "category_create": "categories.manage",
    "category_update": "categories.manage", "category_delete": "categories.manage",
    "stock_ledger": "stock.view", "stock_ledger_excel": "stock.view",
    "stock_ledger_bulk_excel": "stock.view", "stock_adjust_create": "stock.create",
    "stock_adjust_edit": "stock.edit", "stock_adjust_delete": "stock.delete",
    "customer_list": "customers.view", "customer_contacts": "customers.view",
    "customer_list_excel": "customers.view", "customer_detail": "customers.view",
    "customer_create": "customers.create", "customer_update": "customers.edit",
    "customer_delete": "customers.delete", "customer_ledger": "balances.view",
    "customer_ledger_pdf": "reports.export", "customer_ledger_bulk_excel": "reports.export",
    "customer_opening_balance_edit": "balances.opening_edit",
    "customer_adjustment_create": "balances.edit", "customer_adjustment_edit": "balances.edit",
    "customer_adjustment_delete": "balances.edit", "customer_payment_edit": "payments.edit",
    "customer_settlement_detail": "balances.view",
    "customer_settlement_edit": "payments.edit", "customer_settlement_cancel": "payments.edit",
    "customer_settle": "billing.settle", "bill_create": "billing.create",
    "bill_products": "billing.create", "bill_save": "billing.create", "bill_list": "billing.view",
    "bill_list_excel": "reports.export", "bill_detail": "billing.view", "bill_print": "billing.view",
    "bill_edit": "billing.edit", "bill_delete": "billing.delete",
    "bill_add_payment": "payments.create", "held_bill_list": "billing.create",
    "held_bill_save": "billing.create", "held_bill_recall": "billing.create",
    "held_bill_delete": "billing.delete",
    "customer_return_list": "returns.view", "customer_return_create": "returns.manage",
    "customer_return_bills": "returns.manage", "customer_return_detail": "returns.view",
    "customer_return_delete": "returns.manage",
    "cheque_list": "cheques.view", "cheque_list_excel": "reports.export",
    "cheque_maturity_soon": "cheques.view", "cheque_bulk_status": "cheques.manage",
    "cheque_deposit": "cheques.manage", "cheque_hold": "cheques.manage",
    "cheque_bounce": "cheques.manage", "cheque_edit": "cheques.manage",
    "cheque_delete": "cheques.manage", "cash_drawer": "cash_drawer.view",
    "cash_drawer_excel": "reports.export", "cash_drawer_insert": "cash_drawer.manage",
    "cash_drawer_adjust": "cash_drawer.manage", "cash_drawer_edit": "cash_drawer.manage",
    "cash_drawer_delete": "cash_drawer.manage",
    "supplier_bill_list": "supplier_bills.view", "supplier_bill_list_excel": "reports.export",
    "supplier_bill_detail": "supplier_bills.view", "supplier_bill_create": "supplier_bills.manage",
    "supplier_bill_edit": "supplier_bills.manage", "supplier_bill_delete": "supplier_bills.manage",
    "supplier_bill_pay": "supplier_bills.manage", "supplier_quick_create": "suppliers.manage",
    "production_list": "production.view", "production_create": "production.manage",
    "production_edit": "production.manage", "production_delete": "production.manage",
    "petty_cash": "petty_cash.view", "petty_cash_excel": "reports.export",
    "petty_cash_pdf": "reports.export", "petty_cash_expense_create": "petty_cash.manage",
    "petty_cash_expense_edit": "petty_cash.manage", "petty_cash_expense_delete": "petty_cash.manage",
    "petty_cash_reimbursement_create": "petty_cash.manage",
    "petty_cash_reimbursement_edit": "petty_cash.manage", "petty_cash_reimbursement_delete": "petty_cash.manage",
    "vehicle_list": "vehicles.manage", "vehicle_create": "vehicles.manage",
    "vehicle_edit": "vehicles.manage", "vehicle_delete": "vehicles.manage",
    "rider_list": "trips.manage", "rider_create": "trips.manage", "rider_edit": "trips.manage",
    "rider_delete": "trips.manage", "vehicle_trip_list": "trips.manage",
    "vehicle_trip_create": "trips.manage", "vehicle_trip_edit": "trips.manage",
    "vehicle_trip_delete": "trips.manage", "vehicle_trip_pdf": "reports.export",
    "order_list": "orders.view", "order_create": "orders.manage", "order_edit": "orders.manage",
    "order_delete": "orders.manage", "order_detail": "orders.view", "order_set_status": "orders.manage",
    "order_pdf": "reports.export", "order_excel": "reports.export",
    "order_delivery_note_excel": "reports.export", "sales_report": "reports.view",
    "sales_report_pdf": "reports.export", "outstanding_report": "reports.view",
    "outstanding_report_pdf": "reports.export",
    "order_production_check": "orders.view", "order_production_check_data": "orders.view",
    "material_supplier_list": "suppliers.manage", "material_supplier_create": "suppliers.manage",
    "material_supplier_edit": "suppliers.manage", "material_supplier_delete": "suppliers.manage",
    "material_list": "purchasing.manage", "material_create": "purchasing.manage",
    "material_edit": "purchasing.manage", "material_delete": "purchasing.manage",
    "material_purchase_list": "purchasing.manage", "material_purchase_create": "purchasing.manage",
    "material_purchase_detail": "purchasing.manage", "material_purchase_edit": "purchasing.manage",
    "material_purchase_delete": "purchasing.manage", "material_purchase_weigh_add": "purchasing.manage",
    "material_purchase_weigh_edit": "purchasing.manage", "material_purchase_weigh_delete": "purchasing.manage",
    "machine_list": "production.view", "machine_create": "production.manage",
    "machine_edit": "production.manage", "machine_delete": "production.manage",
    "daily_run": "production.manage", "daily_run_history": "production.view",
    "ledger_index": "balances.view",
    "billing_settings": "billing_settings.manage",
    "user_list": "user_management.view", "user_create": "user_management.manage",
    "manager_permissions": "user_management.manage",
    "manager_permissions_for_user": "user_management.manage",
    "permission_templates": "user_management.manage",
    "permission_template_assign": "user_management.manage",
    "user_edit": "user_management.manage", "user_set_password": "user_management.manage",
    "user_deactivate": "user_management.manage", "user_activate": "user_management.manage",
    "user_delete": "user_management.manage",
    "audit_log_list": "audit.view", "audit_log_delete_month": "audit.view",
    "order_delivery_note": "reports.export",
    "db_admin_unlock": "database.manage", "db_admin_index": "database.manage",
    "db_admin_table": "database.manage", "db_admin_row_create": "database.manage",
    "db_admin_row_edit": "database.manage", "db_admin_row_delete": "database.manage",
}


def permission_for_url(url_name, method="GET"):
    if method not in {"GET", "HEAD", "OPTIONS"}:
        if url_name == "customer_contacts":
            return "customers.edit"
        if url_name == "cash_drawer":
            return "cash_drawer.create"
    return URL_PERMISSION_MAP.get(url_name)


def permission_candidates(codename):
    return (codename, *PERMISSION_ALIASES.get(codename, ()))


def sync_permission_registry(apps, schema_editor):
    definition_model = apps.get_model("core", "PermissionDefinition")
    active = {row[1] for row in PERMISSION_REGISTRY}
    for module, codename, name, operation, sensitive in PERMISSION_REGISTRY:
        definition_model.objects.update_or_create(
            codename=codename,
            defaults={
                "module": module, "name": name, "description": name,
                "operation": operation, "is_sensitive": sensitive, "is_active": True,
            },
        )
    definition_model.objects.exclude(codename__in=active).update(is_active=False)


# Task permissions describe actual application actions. Legacy data grants are
# accepted only for the exact operation; a create grant never implies delete.
ADMIN_ONLY_ROUTES = {name for name, code in URL_PERMISSION_MAP.items()
                     if code.startswith(("user_management.", "audit.", "database."))}
PUBLIC_ROUTES = {"landing", "notification_dismiss", "profile", "login", "logout"}
TASK_SPECS = [
    ("categories", "Categories", "categories.manage", "category", {
        "create": "category_create", "edit": "category_update", "delete": "category_delete"}),
    ("production", "Production", "production.manage", "productionentry", {
        "create": "production_create", "edit": "production_edit", "delete": "production_delete"}),
    ("machines", "Machines and Daily Work", "production.manage", "machine", {
        "view": "machine_list", "create": "machine_create", "edit": "machine_edit", "delete": "machine_delete"}),
    ("daily_work", "Machines and Daily Work", "production.manage", "dailymachinerun", {
        "view": "daily_run_history", "edit": "daily_run"}),
    ("supplier_bills", "Supplier Bills", "supplier_bills.manage", "supplierbill", {
        "create": "supplier_bill_create", "edit": "supplier_bill_edit", "delete": "supplier_bill_delete", "pay": "supplier_bill_pay"}),
    ("returns", "Customer Returns", "returns.manage", "customerreturn", {
        "create": "customer_return_create customer_return_bills", "delete": "customer_return_delete"}),
    ("orders", "Orders and Quotations", "orders.manage", "order", {
        "create": "order_create", "edit": "order_edit order_set_status", "delete": "order_delete"}),
    ("cheques", "Cheques", "cheques.manage", "cheque", {
        "edit": "cheque_bulk_status cheque_deposit cheque_hold cheque_bounce cheque_edit", "delete": "cheque_delete"}),
    ("cash_drawer", "Cash Drawer", "cash_drawer.manage", "cashdrawer", {
        "create": "cash_drawer_insert", "edit": "cash_drawer_edit cash_drawer_adjust", "delete": "cash_drawer_delete"}),
    ("petty_cash", "Petty Cash and Expenses", "petty_cash.manage", "pettycashentry", {
        "create": "petty_cash_expense_create petty_cash_reimbursement_create", "edit": "petty_cash_expense_edit petty_cash_reimbursement_edit", "delete": "petty_cash_expense_delete petty_cash_reimbursement_delete"}),
    ("vehicles", "Vehicles", "vehicles.manage", "vehicle", {
        "view": "vehicle_list", "create": "vehicle_create", "edit": "vehicle_edit", "delete": "vehicle_delete"}),
    ("riders", "Riders and Trips", "trips.manage", "rider", {
        "view": "rider_list", "create": "rider_create", "edit": "rider_edit", "delete": "rider_delete"}),
    ("trips", "Riders and Trips", "trips.manage", "vehicletrip", {
        "view": "vehicle_trip_list", "create": "vehicle_trip_create", "edit": "vehicle_trip_edit", "delete": "vehicle_trip_delete"}),
    ("material_suppliers", "Suppliers", "suppliers.manage", "materialsupplier", {
        "view": "material_supplier_list", "create": "material_supplier_create", "edit": "material_supplier_edit", "delete": "material_supplier_delete"}),
    ("materials", "Raw Material Purchasing", "purchasing.manage", "material", {
        "view": "material_list", "create": "material_create", "edit": "material_edit", "delete": "material_delete"}),
    ("purchases", "Raw Material Purchasing", "purchasing.manage", "materialpurchase", {
        "view": "material_purchase_list material_purchase_detail", "create": "material_purchase_create", "edit": "material_purchase_edit", "delete": "material_purchase_delete", "weigh": "material_purchase_weigh_add material_purchase_weigh_edit material_purchase_weigh_delete"}),
]
# Broad manage grants remain compatible, but legacy row grants cannot activate them.
for code in list(PERMISSION_ALIASES):
    if code.endswith(".manage"):
        PERMISSION_ALIASES[code] = ()
PERMISSION_ALIASES["pricing.edit"] = ("data.customerprice.edit",)
PERMISSION_ALIASES["pricing.view"] = ("data.customerprice.view",)
PERMISSION_ALIASES["balances.view"] = ()
PERMISSION_ALIASES["balances.edit"] = ()
for prefix, module, broad, model, operations in TASK_SPECS:
    for operation, routes in operations.items():
        code = f"{prefix}.{operation}"
        if not any(row[1] == code for row in PERMISSION_REGISTRY):
            PERMISSION_REGISTRY.append((module, code, f"{operation.title()} {prefix.replace('_', ' ')}", operation.title(), operation != "view"))
        legacy = (f"data.{model}.{operation}",) if operation in {"view", "create", "edit", "delete"} else ()
        PERMISSION_ALIASES[code] = (broad, *legacy)
        for route in routes.split():
            URL_PERMISSION_MAP[route] = code
# Do not offer raw-table or reserved administration grants as manager tasks.
UNAVAILABLE_MANAGER_CODES = {"balances.set", "payments.delete", "audit.view", "database.manage", "user_management.view", "user_management.manage"}
ASSIGNABLE_CODES = {row[1] for row in PERMISSION_REGISTRY
                    if not row[1].startswith("data.") and row[1] not in UNAVAILABLE_MANAGER_CODES}

# Exports require access to their data as well as export permission.
EXPORT_SCOPES = {
    "product_export_excel": "products.view", "stock_ledger_excel": "stock.view", "stock_ledger_bulk_excel": "stock.view",
    "customer_list_excel": "customers.view", "customer_ledger_pdf": "balances.view", "customer_ledger_bulk_excel": "balances.view",
    "bill_list_excel": "billing.view", "cheque_list_excel": "cheques.view", "cash_drawer_excel": "cash_drawer.view",
    "supplier_bill_list_excel": "supplier_bills.view", "petty_cash_excel": "petty_cash.view", "petty_cash_pdf": "petty_cash.view",
    "vehicle_trip_pdf": "trips.view", "order_pdf": "orders.view", "order_excel": "orders.view", "order_delivery_note": "orders.view",
    "sales_report_pdf": "reports.view", "outstanding_report_pdf": "reports.view",
}
for route in EXPORT_SCOPES:
    URL_PERMISSION_MAP[route] = "reports.export"


def can_access(user, route, method="GET"):
    if not user or not user.is_authenticated or not user.is_active:
        return False
    route = (route or "").removeprefix("core:")
    if user.role == "super_admin":
        return True
    if route in ADMIN_ONLY_ROUTES:
        return False
    if route in PUBLIC_ROUTES:
        return True
    if route == "bill_products":
        return user.has_erp_permission("billing.create") or user.has_erp_permission("billing.edit")
    code = permission_for_url(route, method)
    return bool(code and user.has_erp_permission(code) and (
        route not in EXPORT_SCOPES or user.has_erp_permission(EXPORT_SCOPES[route])))


def with_required_views(codes):
    """Include navigation/detail access needed to complete an assigned task."""
    result = set(codes)
    extra = {"pricing": "products.view", "balances": "customers.view", "payments": "balances.view",
             "billing": "customers.view", "stock": "products.view", "returns": "customers.view",
             "supplier_bills": "customers.view", "purchases": "materials.view"}
    while True:
        before = set(result)
        if "products.create" in result:
            result.add("pricing.create")
        if "payments.create" in result or "billing.settle" in result:
            result.update({"billing.view", "balances.view"})
        if "stock.view" in result:
            result.add("products.view")
        if "purchases.create" in result or "purchases.edit" in result:
            result.add("material_suppliers.view")
        for code in tuple(result):
            prefix, operation = code.split(".", 1)
            view = f"{prefix}.view"
            if operation != "view" and view in ASSIGNABLE_CODES:
                result.add(view)
            if operation != "view" and prefix in extra:
                result.add(extra[prefix])
        if before == result:
            return result
