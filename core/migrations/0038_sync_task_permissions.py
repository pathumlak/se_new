from django.db import migrations


def sync_tasks(apps, schema_editor):
    from core.permissions import sync_permission_registry
    sync_permission_registry(apps, schema_editor)


class Migration(migrations.Migration):
    dependencies = [("core", "0037_sync_full_permission_registry")]
    operations = [migrations.RunPython(sync_tasks, migrations.RunPython.noop)]
