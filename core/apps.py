from django.apps import AppConfig


class CoreConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "core"

    def ready(self):
        """Wire the audit-log signals once the models are loaded.

        Kept deferred to ready() rather than done at module import — signals
        cannot be connected before the app registry has resolved all models,
        and importing them earlier would create a cycle through apps.py.
        """
        from . import audit
        audit.register_signals()
