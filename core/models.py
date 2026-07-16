from django.contrib.auth.models import AbstractUser
from django.db import models


class User(AbstractUser):
    """Application user. Accounts are seeded, never self-registered."""

    class Role(models.TextChoices):
        SUPER_ADMIN = "super_admin", "Super Admin"
        MANAGER = "manager", "Manager"

    role = models.CharField(max_length=20, choices=Role.choices, default=Role.MANAGER)

    def __str__(self):
        return f"{self.username} ({self.get_role_display()})"


class Category(models.Model):
    name = models.CharField(max_length=100, unique=True)
    description = models.TextField(blank=True)

    class Meta:
        verbose_name_plural = "categories"
        ordering = ["name"]

    def __str__(self):
        return self.name


class Product(models.Model):
    name = models.CharField(max_length=150)
    size = models.CharField(max_length=50, blank=True)
    qty = models.DecimalField(max_digits=12, decimal_places=3, default=0)
    category = models.ForeignKey(
        Category,
        on_delete=models.PROTECT,
        related_name="products",
    )
    default_price = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name", "size"]

    def __str__(self):
        return f"{self.name} {self.size}".strip()


class Customer(models.Model):
    """A trading party. Suppliers are customers with is_supplier=True."""

    name = models.CharField(max_length=150)
    phone = models.CharField(max_length=30, blank=True)
    address = models.TextField(blank=True)
    credit_limit = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    balance = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    is_supplier = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class CustomerPrice(models.Model):
    """Per-customer negotiated price, overriding Product.default_price."""

    customer = models.ForeignKey(
        Customer,
        on_delete=models.CASCADE,
        related_name="custom_prices",
    )
    product = models.ForeignKey(
        Product,
        on_delete=models.CASCADE,
        related_name="customer_prices",
    )
    unit_price = models.DecimalField(max_digits=12, decimal_places=2)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ("customer", "product")
        ordering = ["customer", "product"]

    def __str__(self):
        return f"{self.customer} · {self.product} @ {self.unit_price}"


class Bill(models.Model):
    class PaymentType(models.TextChoices):
        FULL_CASH = "full_cash", "Full Cash"
        FULL_CHEQUE = "full_cheque", "Full Cheque"
        PARTIAL = "partial", "Partial"
        MIXED = "mixed", "Mixed"
        PAY_LATER = "pay_later", "Pay Later"

    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        UNPAID = "unpaid", "Unpaid"
        PARTIAL = "partial", "Partially Paid"
        PAID = "paid", "Paid"
        CANCELLED = "cancelled", "Cancelled"

    customer = models.ForeignKey(
        Customer,
        on_delete=models.PROTECT,
        related_name="bills",
    )
    bill_date = models.DateField()
    subtotal = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    total_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    paid_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    # Signed: how this bill moved the customer's running balance.
    balance_change = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    payment_type = models.CharField(max_length=20, choices=PaymentType.choices)
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.UNPAID
    )
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["-bill_date", "-id"]

    def __str__(self):
        return f"Bill #{self.pk} · {self.customer} · {self.total_amount}"


class BillItem(models.Model):
    bill = models.ForeignKey(
        Bill,
        on_delete=models.CASCADE,
        related_name="items",
    )
    product = models.ForeignKey(
        Product,
        on_delete=models.PROTECT,
        related_name="bill_items",
    )
    qty = models.DecimalField(max_digits=12, decimal_places=3)
    unit_price = models.DecimalField(max_digits=12, decimal_places=2)
    line_total = models.DecimalField(max_digits=12, decimal_places=2)

    def __str__(self):
        return f"{self.product} × {self.qty} = {self.line_total}"


class Payment(models.Model):
    class Method(models.TextChoices):
        CASH = "cash", "Cash"
        CHEQUE = "cheque", "Cheque"
        TRANSFER = "transfer", "Transfer"

    class Account(models.TextChoices):
        SENOVKA = "senovka", "Senovka"
        DINUSHA = "dinusha", "Dinusha"

    bill = models.ForeignKey(
        Bill,
        on_delete=models.CASCADE,
        related_name="payments",
    )
    method = models.CharField(max_length=20, choices=Method.choices)
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    account = models.CharField(max_length=20, choices=Account.choices, blank=True)
    paid_at = models.DateTimeField()

    class Meta:
        ordering = ["-paid_at", "-id"]

    def __str__(self):
        return f"{self.get_method_display()} {self.amount} · Bill #{self.bill_id}"


class Cheque(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        DEPOSITED = "deposited", "Deposited"
        BOUNCED = "bounced", "Bounced"
        HELD = "held", "Held"

    payment = models.ForeignKey(
        Payment,
        on_delete=models.CASCADE,
        related_name="cheques",
    )
    customer = models.ForeignKey(
        Customer,
        on_delete=models.PROTECT,
        related_name="cheques",
    )
    cheque_no = models.CharField(max_length=50)
    bank_name = models.CharField(max_length=100)
    branch = models.CharField(max_length=100, blank=True)
    acc_no = models.CharField(max_length=50, blank=True)
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    received_date = models.DateField()
    maturity_date = models.DateField()
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.PENDING
    )
    # Re-presentation date agreed with the customer after a bounce.
    bounce_new_date = models.DateField(null=True, blank=True)

    class Meta:
        ordering = ["maturity_date", "-id"]

    def __str__(self):
        return f"Cheque {self.cheque_no} · {self.bank_name} · {self.amount}"


class CashTransfer(models.Model):
    class Account(models.TextChoices):
        SENOVKA = "senovka", "Senovka"
        DINUSHA = "dinusha", "Dinusha"

    payment = models.ForeignKey(
        Payment,
        on_delete=models.CASCADE,
        related_name="transfers",
    )
    to_account = models.CharField(max_length=20, choices=Account.choices)
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    transferred_at = models.DateTimeField()

    class Meta:
        ordering = ["-transferred_at", "-id"]

    def __str__(self):
        return f"{self.amount} to {self.get_to_account_display()}"


class CashDrawer(models.Model):
    class TxnType(models.TextChoices):
        IN = "in", "In"
        OUT = "out", "Out"
        TRANSFER = "transfer", "Transfer"

    txn_date = models.DateField()
    txn_type = models.CharField(max_length=20, choices=TxnType.choices)
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    reason = models.CharField(max_length=255, blank=True)
    bill = models.ForeignKey(
        Bill,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="cash_drawer_entries",
    )

    class Meta:
        ordering = ["-txn_date", "-id"]

    def __str__(self):
        return f"{self.get_txn_type_display()} {self.amount} on {self.txn_date}"


class SupplierBill(models.Model):
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        UNPAID = "unpaid", "Unpaid"
        PARTIAL = "partial", "Partially Paid"
        PAID = "paid", "Paid"
        CANCELLED = "cancelled", "Cancelled"

    supplier = models.ForeignKey(
        Customer,
        on_delete=models.PROTECT,
        related_name="supplier_bills",
    )
    bill_date = models.DateField()
    total_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    paid_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.UNPAID
    )
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["-bill_date", "-id"]

    def __str__(self):
        return f"Supplier Bill #{self.pk} · {self.supplier} · {self.total_amount}"


class SupplierBillItem(models.Model):
    supplier_bill = models.ForeignKey(
        SupplierBill,
        on_delete=models.CASCADE,
        related_name="items",
    )
    product = models.ForeignKey(
        Product,
        on_delete=models.PROTECT,
        related_name="supplier_bill_items",
    )
    qty = models.DecimalField(max_digits=12, decimal_places=3)
    unit_price = models.DecimalField(max_digits=12, decimal_places=2)
    line_total = models.DecimalField(max_digits=12, decimal_places=2)

    def __str__(self):
        return f"{self.product} × {self.qty} = {self.line_total}"


class ProductionEntry(models.Model):
    product = models.ForeignKey(
        Product,
        on_delete=models.PROTECT,
        related_name="production_entries",
    )
    production_date = models.DateField()
    qty_produced = models.DecimalField(max_digits=12, decimal_places=3)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name_plural = "production entries"
        ordering = ["-production_date", "-id"]

    def __str__(self):
        return f"{self.product} × {self.qty_produced} on {self.production_date}"
