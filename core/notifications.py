"""Live notification feed for the topbar bell.

Three sources: low stock, out of stock, and cheques maturing within
CHEQUE_WARNING_DAYS. Each notification carries a stable `key` so the front-end
can tell a notification it has already toasted from a fresh one, and so the
operator's dismissal can outlive a page navigation.

Dismissals are stored in the session (not the database) because the notice
itself is derived from live data — the dismissal only has to survive a few
days, and putting it in the session keeps it per-user without needing a new
model or a migration. Expired dismissals are garbage-collected on read.
"""

from datetime import datetime, timedelta

from django.urls import reverse
from django.utils import timezone


#: How long a "×"-dismissal on a single notification lasts. The user asked
#: for three days: long enough that the same alert doesn't hound them all
#: week, short enough that a genuinely persistent problem (a product that
#: stayed empty) resurfaces on its own.
DISMISS_DAYS = 3

#: Session key holding {notification_key: iso_datetime_expires}. Kept
#: narrow rather than nesting it inside a broader "ui" dict — the session
#: is per-user already, and a flat key is one less thing for a later
#: migration to reason about.
DISMISS_SESSION_KEY = "senovka_dismissed_notifications"

#: Products at or below this qty band trigger a "low stock" notice.
#: Kept in step with settings.LOW_STOCK_THRESHOLD via the caller.


def _now():
    return timezone.now()


def _isoformat(dt):
    return dt.isoformat()


def _parse_iso(value):
    """Cheap ISO parse. Returns None on anything not parseable, which is
    also how we recover from a session cookie carrying garbage — the row
    is dropped and the notification resurfaces."""
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None


def _load_dismissals(session):
    """Read the dismissals map, dropping any whose `until` is in the past.

    We rewrite the session with the pruned map only if the pruning actually
    changed something — every request touches this, and rewriting a session
    every request would upset the sticky-session cache without cause.
    """
    raw = session.get(DISMISS_SESSION_KEY, {})
    if not isinstance(raw, dict):
        return {}

    now = _now()
    kept = {}
    changed = False
    for key, until in raw.items():
        parsed = _parse_iso(until)
        if parsed and parsed > now:
            kept[key] = until
        else:
            changed = True

    if changed:
        session[DISMISS_SESSION_KEY] = kept
        session.modified = True

    return kept


def dismiss(session, key):
    """Mark `key` dismissed for DISMISS_DAYS.

    Merges into the existing map rather than overwriting so a burst of
    dismisses in one request all land. Session.modified is nudged
    explicitly because we mutate the dict in place — Django's session
    middleware only autosaves on assignment.
    """
    dismissals = _load_dismissals(session)
    until = _now() + timedelta(days=DISMISS_DAYS)
    dismissals[key] = _isoformat(until)
    session[DISMISS_SESSION_KEY] = dismissals
    session.modified = True


def _low_stock_notifications(low_threshold):
    """One row per product currently in the 'low' band (qty > 0, <= threshold).

    Deferred import: this module is loaded by a context processor that runs
    on every request. Importing the models at the module level would create
    an import cycle with `views` on cold start.
    """
    from .models import Product

    qs = (
        Product.objects.filter(is_active=True, qty__gt=0, qty__lte=low_threshold)
        .order_by("qty", "name")
        .only("id", "name", "size", "qty")
    )

    items = []
    for product in qs:
        label = f"{product.name} {product.size}".strip()
        items.append(
            {
                "key": f"low_stock:{product.pk}",
                "kind": "low_stock",
                "level": "warning",
                "icon": "package",
                "title": f"Low stock · {label}",
                "body": f"Only {product.qty} left — reorder soon",
                "url": reverse("core:product_prices", args=[product.pk]),
                "timestamp": _isoformat(_now()),
            }
        )
    return items


def _out_of_stock_notifications():
    from .models import Product

    qs = (
        Product.objects.filter(is_active=True, qty__lte=0)
        .order_by("qty", "name")
        .only("id", "name", "size", "qty")
    )

    items = []
    for product in qs:
        label = f"{product.name} {product.size}".strip()
        items.append(
            {
                "key": f"out_of_stock:{product.pk}",
                "kind": "out_of_stock",
                "level": "danger",
                "icon": "package",
                "title": f"Out of stock · {label}",
                "body": (
                    "Shelf is empty."
                    if product.qty == 0
                    else f"Oversold by {abs(product.qty)}."
                ),
                "url": reverse("core:product_prices", args=[product.pk]),
                "timestamp": _isoformat(_now()),
            }
        )
    return items


def _cheque_notifications(warning_days):
    from .models import Cheque

    today = timezone.localdate()
    horizon = today + timedelta(days=warning_days)

    qs = (
        Cheque.objects.filter(
            maturity_date__lte=horizon, status=Cheque.Status.PENDING
        )
        .select_related("customer")
        .order_by("maturity_date")
    )

    items = []
    for cheque in qs:
        days_left = (cheque.maturity_date - today).days
        if days_left < 0:
            body = f"{cheque.customer.name} · Overdue by {abs(days_left)} day{'s' if abs(days_left) != 1 else ''}"
            level = "danger"
        elif days_left == 0:
            body = f"{cheque.customer.name} · Due today"
            level = "warning"
        else:
            body = f"{cheque.customer.name} · Due in {days_left} day{'s' if days_left != 1 else ''}"
            level = "warning"

        items.append(
            {
                "key": f"cheque:{cheque.pk}:{cheque.maturity_date.isoformat()}",
                "kind": "cheque",
                "level": level,
                "icon": "cheque",
                "title": f"Cheque #{cheque.cheque_no} · Rs {cheque.amount}",
                "body": body,
                "url": reverse("core:cheque_list") + "?status=pending",
                "timestamp": _isoformat(_now()),
            }
        )
    return items


#: Order the levels are ranked by when sorting the panel.
_LEVEL_RANK = {"danger": 0, "warning": 1, "info": 2}


def build_notifications(session, low_threshold, warning_days):
    """The whole feed for one request.

    Returns (visible, total_before_dismiss).  `visible` is the list the
    bell renders, dismissals stripped. `total_before_dismiss` is what the
    UI needs to answer "is anything happening at all?" — mostly a debug
    knob, kept because the JS wants to know when the feed is genuinely
    empty vs merely all-dismissed.
    """
    all_items = (
        _out_of_stock_notifications()
        + _low_stock_notifications(low_threshold)
        + _cheque_notifications(warning_days)
    )

    all_items.sort(key=lambda item: (_LEVEL_RANK.get(item["level"], 9), item["title"]))
    total = len(all_items)

    dismissals = _load_dismissals(session)
    visible = [item for item in all_items if item["key"] not in dismissals]

    return visible, total
