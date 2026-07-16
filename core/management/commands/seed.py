"""Seed Senovka ERP with baseline demo data.

Idempotent: every row is created via get_or_create, so re-running leaves
existing data untouched. Run with:

    python manage.py seed
"""

from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.db import transaction

from core.models import Category, Customer, CustomerPrice, Product

USERS = [
    {
        "username": "admin",
        "password": "admin123",
        "role": "super_admin",
        "email": "admin@senovka.local",
        "is_staff": True,
        "is_superuser": True,
    },
    {
        "username": "manager",
        "password": "manager123",
        "role": "manager",
        "email": "manager@senovka.local",
        "is_staff": True,
        "is_superuser": False,
    },
]

CATEGORIES = [
    ("PVC Pipes", "Rigid PVC pressure and drainage pipes"),
    ("PVC Fittings", "Elbows, tees, sockets and reducers"),
    ("HDPE", "High-density polyethylene pipes and coils"),
    ("Tanks", "Water storage tanks"),
    ("Accessories", "Solvents, tapes, clips and sundries"),
]

# (name, size, category, qty, default_price)
PRODUCTS = [
    ("PVC Pressure Pipe", "50mm", "PVC Pipes", "320.000", "850.00"),
    ("PVC Pressure Pipe", "110mm", "PVC Pipes", "185.000", "2450.00"),
    ("PVC Drainage Pipe", "160mm", "PVC Pipes", "140.000", "3900.00"),
    ("PVC Elbow 90", "50mm", "PVC Fittings", "480.000", "120.00"),
    ("PVC Tee", "110mm", "PVC Fittings", "260.000", "380.00"),
    ("HDPE Pipe", "63mm", "HDPE", "215.000", "1650.00"),
    ("HDPE Coil", "32mm", "HDPE", "130.000", "750.00"),
    ("Water Tank", "1000L", "Tanks", "105.000", "4800.00"),
    ("Water Tank", "500L", "Tanks", "115.000", "2900.00"),
    ("Solvent Cement", "500ml", "Accessories", "460.000", "950.00"),
]

# (name, phone, address, credit_limit, is_supplier)
CUSTOMERS = [
    ("Nimal Hardware", "0771234567", "No. 45, Peradeniya Rd, Kandy", "50000.00", False),
    ("Sunrise Traders", "0712345678", "128/A, Galle Rd, Colombo 03", "75000.00", False),
    ("Chathura Stores", "0723334455", "12, Beach Rd, Matara", "10000.00", False),
    ("Perera Enterprises", "0779876543", "88, Main St, Galle", "100000.00", True),
    ("Lanka Pipe Supplies", "0765554433", "301, Kandy Rd, Gampaha", "60000.00", True),
]

# customer -> [((product name, size), unit_price)] — negotiated rates below list price.
CUSTOMER_PRICES = {
    "Nimal Hardware": [
        (("PVC Pressure Pipe", "50mm"), "810.00"),
        (("PVC Elbow 90", "50mm"), "110.00"),
        (("Solvent Cement", "500ml"), "900.00"),
    ],
    "Sunrise Traders": [
        (("PVC Pressure Pipe", "110mm"), "2350.00"),
        (("PVC Tee", "110mm"), "355.00"),
        (("Water Tank", "1000L"), "4600.00"),
        (("HDPE Pipe", "63mm"), "1580.00"),
    ],
    "Chathura Stores": [
        (("PVC Drainage Pipe", "160mm"), "3800.00"),
        (("HDPE Coil", "32mm"), "720.00"),
        (("Water Tank", "500L"), "2800.00"),
    ],
    "Perera Enterprises": [
        (("PVC Pressure Pipe", "50mm"), "790.00"),
        (("HDPE Pipe", "63mm"), "1550.00"),
        (("Water Tank", "1000L"), "4500.00"),
    ],
    "Lanka Pipe Supplies": [
        (("PVC Elbow 90", "50mm"), "105.00"),
        (("PVC Tee", "110mm"), "345.00"),
        (("Solvent Cement", "500ml"), "880.00"),
    ],
}


class Command(BaseCommand):
    help = "Seed baseline users, categories, products, customers and custom prices."

    @transaction.atomic
    def handle(self, *args, **options):
        counts = {}

        counts["users"] = self._seed_users()
        categories, counts["categories"] = self._seed_categories()
        products, counts["products"] = self._seed_products(categories)
        customers, counts["customers"] = self._seed_customers()
        counts["prices"] = self._seed_customer_prices(customers, products)

        self._print_summary(counts)

    def _seed_users(self):
        User = get_user_model()
        created = existing = 0

        for spec in USERS:
            user, was_created = User.objects.get_or_create(
                username=spec["username"],
                defaults={
                    "email": spec["email"],
                    "role": spec["role"],
                    "is_staff": spec["is_staff"],
                    "is_superuser": spec["is_superuser"],
                },
            )
            # Passwords are re-applied on every run so the documented seed
            # credentials always work, even for accounts created earlier.
            user.set_password(spec["password"])
            user.role = spec["role"]
            user.is_staff = spec["is_staff"]
            user.is_superuser = spec["is_superuser"]
            user.save()

            if was_created:
                created += 1
            else:
                existing += 1

        return created, existing

    def _seed_categories(self):
        created = existing = 0
        categories = {}

        for name, description in CATEGORIES:
            category, was_created = Category.objects.get_or_create(
                name=name,
                defaults={"description": description},
            )
            categories[name] = category
            if was_created:
                created += 1
            else:
                existing += 1

        return categories, (created, existing)

    def _seed_products(self, categories):
        created = existing = 0
        products = {}

        for name, size, category_name, qty, price in PRODUCTS:
            product, was_created = Product.objects.get_or_create(
                name=name,
                size=size,
                defaults={
                    "category": categories[category_name],
                    "qty": Decimal(qty),
                    "default_price": Decimal(price),
                    "is_active": True,
                },
            )
            products[(name, size)] = product
            if was_created:
                created += 1
            else:
                existing += 1

        return products, (created, existing)

    def _seed_customers(self):
        created = existing = 0
        customers = {}

        for name, phone, address, credit_limit, is_supplier in CUSTOMERS:
            customer, was_created = Customer.objects.get_or_create(
                name=name,
                defaults={
                    "phone": phone,
                    "address": address,
                    "credit_limit": Decimal(credit_limit),
                    "balance": Decimal("0.00"),
                    "is_supplier": is_supplier,
                    "is_active": True,
                },
            )
            customers[name] = customer
            if was_created:
                created += 1
            else:
                existing += 1

        return customers, (created, existing)

    def _seed_customer_prices(self, customers, products):
        created = existing = 0

        for customer_name, entries in CUSTOMER_PRICES.items():
            for product_key, unit_price in entries:
                _, was_created = CustomerPrice.objects.get_or_create(
                    customer=customers[customer_name],
                    product=products[product_key],
                    defaults={"unit_price": Decimal(unit_price)},
                )
                if was_created:
                    created += 1
                else:
                    existing += 1

        return created, existing

    def _print_summary(self, counts):
        User = get_user_model()

        self.stdout.write("")
        self.stdout.write(self.style.MIGRATE_HEADING("Seed summary"))
        self.stdout.write(f"  {'':18}{'created':>9}{'existing':>10}{'total':>8}")

        rows = [
            ("Users", counts["users"], User.objects.count()),
            ("Categories", counts["categories"], Category.objects.count()),
            ("Products", counts["products"], Product.objects.count()),
            ("Customers", counts["customers"], Customer.objects.count()),
            ("Customer prices", counts["prices"], CustomerPrice.objects.count()),
        ]
        for label, (created, existing), total in rows:
            self.stdout.write(f"  {label:18}{created:>9}{existing:>10}{total:>8}")

        suppliers = Customer.objects.filter(is_supplier=True).count()
        self.stdout.write("")
        self.stdout.write(f"  Customers flagged as suppliers: {suppliers}")
        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS("Seeding complete."))
        self.stdout.write("  Login: admin / admin123  (super_admin)")
        self.stdout.write("         manager / manager123  (manager)")
