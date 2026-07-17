"""Seed the initial user accounts.

There is no public registration flow in Senovka ERP, so accounts are created
here. Passwords are read from the environment when present; the fallbacks are
development-only and must not be used on a deployed instance.
"""

import os

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand

SEED_USERS = [
    {
        "username": "Dushan",
        "email": "dushan@senovka.com",
        "password_env": "SENOVKA_ADMIN_PASSWORD",
        "default_password": "Dushan123",
        "role": "super_admin",
        "is_staff": True,
        "is_superuser": True,
    },
    {
        "username": "Dinusha",
        "email": "dinusha@senovka.com",
        "password_env": "SENOVKA_MANAGER_PASSWORD",
        "default_password": "Dinusha123",
        "role": "manager",
        "is_staff": True,
        "is_superuser": False,
    },
    {
        "username": "Udara",
        "email": "udara@senovka.com",
        "password_env": "SENOVKA_MANAGER_PASSWORD",
        "default_password": "Udara123",
        "role": "manager",
        "is_staff": True,
        "is_superuser": False,
    }
]


class Command(BaseCommand):
    help = "Create the seeded user accounts (idempotent)."

    def handle(self, *args, **options):
        User = get_user_model()

        for spec in SEED_USERS:
            password = os.environ.get(spec["password_env"], spec["default_password"])
            user, created = User.objects.get_or_create(
                username=spec["username"],
                defaults={
                    "email": spec["email"],
                    "role": spec["role"],
                    "is_staff": spec["is_staff"],
                    "is_superuser": spec["is_superuser"],
                },
            )
            if created:
                user.set_password(password)
                user.save()
                self.stdout.write(self.style.SUCCESS(f"Created user '{user.username}'"))
            else:
                self.stdout.write(f"User '{user.username}' already exists — skipped")
