from django.conf import settings

from .notifications import build_notifications


def current_role(request):
    """Expose the signed-in user's role to every template as `current_role`.

    Anonymous users get None, so templates can guard with a plain
    `{% if current_role == 'super_admin' %}` without raising.
    """
    user = getattr(request, "user", None)
    role = getattr(user, "role", None) if user and user.is_authenticated else None
    return {
        "current_role": role,
        "is_super_admin": role == "super_admin",
    }


def notifications(request):
    """The topbar bell feed, computed per request.

    Anonymous or the login page → empty payload; there is no bell to render
    there and touching the queries wastes a round-trip on the sign-in path.
    """
    user = getattr(request, "user", None)
    if not (user and user.is_authenticated):
        return {"notifications": [], "notification_count": 0}

    #: CHEQUE_WARNING_DAYS is defined in views.py; recomputing it here would
    #: risk drift, but importing views at module import time creates a cycle
    #: through decorators → models → views. Defer the import.
    from .views import CHEQUE_WARNING_DAYS

    visible, _total = build_notifications(
        request.session,
        low_threshold=settings.LOW_STOCK_THRESHOLD,
        warning_days=CHEQUE_WARNING_DAYS,
    )
    return {
        "notifications": visible,
        "notification_count": len(visible),
    }
