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


def cash_drawer_balance(request):
    """Expose the live drawer balance to every template, for the topbar pill.

    Anonymous users (just the login page) get None so it can skip rendering
    without touching the database.
    """
    user = getattr(request, "user", None)
    if not (user and user.is_authenticated):
        return {"cash_balance": None}

    # Deferred for the same reason as the import in `notifications` below:
    # views.py imports decorators.py imports models.py, so importing views at
    # module load time here would be a cycle.
    from .views import _cash_drawer_balance

    return {"cash_balance": _cash_drawer_balance()}


def notifications(request):
    """The topbar bell feed, computed per request.

    Anonymous or the login page → empty payload; there is no bell to render
    there and touching the queries wastes a round-trip on the sign-in path.
    """
    user = getattr(request, "user", None)
    if not (user and user.is_authenticated):
        return {"notifications": [], "notification_count": 0}

    #: CHEQUE_WARNING_DAYS and ORDER_FOLLOWUP_DAYS are defined in views.py;
    #: recomputing them here would risk drift, but importing views at module
    #: import time creates a cycle through decorators → models → views.
    #: Defer the import.
    from .views import CHEQUE_WARNING_DAYS, ORDER_FOLLOWUP_DAYS

    visible, _total = build_notifications(
        request.session,
        low_threshold=settings.LOW_STOCK_THRESHOLD,
        warning_days=CHEQUE_WARNING_DAYS,
        order_followup_days=ORDER_FOLLOWUP_DAYS,
    )
    return {
        "notifications": visible,
        "notification_count": len(visible),
    }
