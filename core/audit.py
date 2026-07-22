"""System-wide audit log wiring.

Every business write across the app produces one `AuditLog` row: a create, an
update or a delete, with the user who did it, the row it touched, the request
path and the client IP. Nothing here is called from views directly — the
whitelist below (`_LOGGED_MODELS`) drives `post_save` and `post_delete` signal
handlers that fire automatically the moment a row is written.

There is exactly one moving part the views need to cooperate with: the
`CurrentRequestMiddleware`. Signal handlers do not know which user fired the
save — Django doesn't hand it to them — so we stash the current request on a
thread-local at the start of every request and read it back inside the handler.
The alternative (pushing request into every ORM call) would require touching
every write site in the project, which is exactly what a signals-based
approach exists to avoid.

Adding a new model to the log is one line: append its name to
`_LOGGED_MODELS`. The handler infers a sensible summary from `__str__`, so no
per-model plumbing is needed for the common case.
"""

import threading

from django.db.models.signals import post_delete, post_save

# Per-thread stash. In a WSGI worker each request runs on its own thread, so
# stashing the request here gives signal handlers a stable reference for the
# lifetime of one request. In async views this would need contextvars — the
# app is synchronous WSGI throughout, so the simpler lock is enough.
_local = threading.local()


class CurrentRequestMiddleware:
    """Stash the current request so signal handlers can read `request.user`.

    Django's ORM signals fire in a context with no access to the request that
    triggered them. Rather than plumb the request through every view (and
    every helper that saves a row), we tuck a reference away here on entry
    and pop it on exit. The thread-local is scoped per WSGI worker thread,
    so a second concurrent request always gets its own copy.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        _local.request = request
        try:
            return self.get_response(request)
        finally:
            _local.request = None


def _current_request():
    return getattr(_local, "request", None)


def _client_ip(request):
    """Best-effort client IP.

    X-Forwarded-For arrives set by Nginx in production. Only the left-most
    entry is trustworthy — the rest are appended by proxies and can be spoofed
    by a client. In dev there is no XFF, so we fall back to REMOTE_ADDR.
    """
    if not request:
        return None
    xff = request.META.get("HTTP_X_FORWARDED_FOR", "")
    if xff:
        return xff.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR") or None


#: Models whose writes generate an audit row. Names are strings so this file
#: can be imported without pulling in every model at parse time; the app's
#: `ready()` hook resolves them.
_LOGGED_MODELS = [
    # Sales / money
    "Bill", "BillSettlement", "Payment", "Cheque", "CashDrawer",
    "CashTransfer", "CustomerBalanceAdjustment",
    # Customers & catalogue
    "Customer", "Category", "Product", "CustomerPrice",
    # Suppliers & purchasing
    "SupplierBill", "MaterialSupplier", "Material",
    "MaterialPurchase", "MaterialWeighEntry",
    # Stock / production
    "ProductionEntry", "StockAdjustment",
    # Ops
    "Machine", "DailyMachineRun", "DailyOtherWork",
    "Vehicle", "Rider", "VehicleTrip",
    # Petty cash
    "PettyCashEntry", "PettyCashReimbursement",
    # Quotations
    "Order",
    # Access
    "User",
]

#: Models we deliberately do NOT log to keep the log readable.
#: - `AuditLog` itself would loop; guarded by name in the handler.
#: - `HeldBill` is a draft parking table; the final save creates a Bill row
#:   which is what people actually audit.
#: - `BillItem` / `SupplierBillItem` / `OrderItem` / `MaterialPurchaseItem`
#:   are child rows; the parent Bill/Order/Purchase carries the meaningful
#:   audit event.
#: - `ReferenceCounter` is an internal counter, not a business record.
#: - `PettyCashFund` is derived (recalculated after entry writes), not
#:   directly edited.
#: - `BillEditAudit` is itself an audit row of a different kind.


def _resolve_logged_models():
    """Turn the string whitelist into a set of concrete Model classes."""
    from django.apps import apps

    resolved = set()
    for name in _LOGGED_MODELS:
        try:
            resolved.add(apps.get_model("core", name))
        except LookupError:
            # A whitelist name that no longer maps to a real model is not
            # worth crashing app startup over — surface it silently.
            continue
    return resolved


_logged_model_set = None


def _is_logged(model_class):
    global _logged_model_set
    if _logged_model_set is None:
        _logged_model_set = _resolve_logged_models()
    return model_class in _logged_model_set


def _target_label(instance):
    """The one-line human name of the row that was saved.

    Falls back through str(), the primary key, and finally the class name.
    Truncated to fit the model's column width so an over-long __str__
    doesn't 500 the write path.
    """
    try:
        label = str(instance)
    except Exception:  # __str__ can hit lazy relations that fail — swallow.
        label = f"{instance.__class__.__name__} #{instance.pk}"
    return (label or "")[:255]


def _record(action, instance):
    """Write one AuditLog row for `instance`.

    Skips silently when there is no active request (management commands,
    migrations, shell writes) — those are legitimate but not user-driven and
    logging them would poison the "who did this" question.
    """
    request = _current_request()
    if request is None:
        return

    # Deferred import so this module can be imported at app-load time before
    # models are ready.
    from .models import AuditLog

    if not _is_logged(instance.__class__):
        return
    # Guard against a signal firing on an AuditLog row itself — writing an
    # audit for the audit would recurse forever.
    if instance.__class__.__name__ == "AuditLog":
        return

    user = getattr(request, "user", None)
    if user is not None and not getattr(user, "is_authenticated", False):
        user = None

    AuditLog.objects.create(
        action=action,
        user=user,
        target_type=instance.__class__.__name__,
        target_id=getattr(instance, "pk", None),
        target_label=_target_label(instance),
        summary=f"{instance.__class__.__name__} {action}d",
        ip_address=_client_ip(request),
        path=(request.path or "")[:255],
    )


def _on_post_save(sender, instance, created, **kwargs):
    _record(
        AuditLog_action_choice_CREATE if created else AuditLog_action_choice_UPDATE,
        instance,
    )


def _on_post_delete(sender, instance, **kwargs):
    _record(AuditLog_action_choice_DELETE, instance)


# The action strings are duplicated here rather than looked up on
# `AuditLog.Action` at import time — importing models at module load creates a
# cycle with apps.py. These four constants are the canonical values.
AuditLog_action_choice_CREATE = "create"
AuditLog_action_choice_UPDATE = "update"
AuditLog_action_choice_DELETE = "delete"


def register_signals():
    """Attach signals for every whitelisted model.

    Called once from `CoreConfig.ready()`. Uniqueness of `dispatch_uid` is
    important — Django loads modules multiple times in some autoreload
    configurations, and a duplicate connect would double-log every write.
    """
    for model in _resolve_logged_models():
        post_save.connect(
            _on_post_save,
            sender=model,
            dispatch_uid=f"audit:save:{model._meta.label}",
        )
        post_delete.connect(
            _on_post_delete,
            sender=model,
            dispatch_uid=f"audit:delete:{model._meta.label}",
        )
