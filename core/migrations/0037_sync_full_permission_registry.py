from django.db import migrations


def sync_registry(apps, schema_editor):
    from core.permissions import sync_permission_registry

    sync_permission_registry(apps, schema_editor)


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0036_permissiondefinition_permissiontemplate_and_more"),
    ]

    operations = [
        migrations.RunPython(sync_registry, migrations.RunPython.noop),
    ]
