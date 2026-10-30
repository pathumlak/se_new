from django.core.management.base import BaseCommand

from core.models import PermissionDefinition
from core.permissions import PERMISSION_REGISTRY


class Command(BaseCommand):
    help = "Synchronize the ERP permission registry without granting new permissions."

    def handle(self, *args, **options):
        active = {row[1] for row in PERMISSION_REGISTRY}
        for module, codename, name, operation, sensitive in PERMISSION_REGISTRY:
            PermissionDefinition.objects.update_or_create(
                codename=codename,
                defaults={
                    "module": module, "name": name, "description": name,
                    "operation": operation, "is_sensitive": sensitive, "is_active": True,
                },
            )
        count = PermissionDefinition.objects.exclude(codename__in=active).update(
            is_active=False
        )
        self.stdout.write(self.style.SUCCESS(f"Permission registry synchronized ({count} retired)."))
