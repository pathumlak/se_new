"""Shared helpers that are not views and not models.

Only the month filter lives here so far. It is deliberately separate from the
`from_date`/`to_date` range filter the bill list and the reports use: those
answer "show me this stretch of days", which is a reporting question, while
this one answers "show me this month, like every other month", which is how
the operational logs are read. A petty cash fund *is* a month; a vehicle log
is closed off and totalled a month at a time. Forcing those onto a free date
range would mean the operator picking the 1st and the 31st by hand every time
they opened the page.
"""

from dataclasses import dataclass
from datetime import date, timedelta

from django.utils import timezone

#: What ?month= carries to mean "every month at once".
ALL_TIME = "all"


def month_start(value):
    """The first day of the month `value` falls in."""
    return value.replace(day=1)


def next_month(value):
    """The first day of the month after the one `value` falls in."""
    if value.month == 12:
        return date(value.year + 1, 1, 1)
    return date(value.year, value.month + 1, 1)


def previous_month(value):
    """The first day of the month before the one `value` falls in."""
    first = month_start(value)
    if first.month == 1:
        return date(first.year - 1, 12, 1)
    return date(first.year, first.month - 1, 1)


@dataclass(frozen=True)
class MonthFilter:
    """One month, or all of time, as chosen by ?month=.

    Frozen because a view hands this straight to a template and to several
    querysets; a filter that could be rewritten halfway down a view is a filter
    that can disagree with the heading printed above the table.
    """

    #: First day of the chosen month. None when showing all time — there is no
    #: single month to point at, and None makes forgetting to check
    #: `is_all_time` fail loudly rather than silently pick January.
    month: date | None

    @property
    def is_all_time(self):
        return self.month is None

    @property
    def start(self):
        """First day of the month, inclusive. None when all-time."""
        return self.month

    @property
    def end(self):
        """Last day of the month, inclusive. None when all-time.

        Inclusive rather than the 1st of the next month, because it is compared
        against DateFields with __lte. A DateTimeField would want the exclusive
        upper bound instead — none of the models filtered here have one.
        """
        if self.month is None:
            return None
        return next_month(self.month) - timedelta(days=1)

    @property
    def previous(self):
        """First day of the preceding month. None when all-time."""
        return None if self.month is None else previous_month(self.month)

    @property
    def next(self):
        """First day of the following month. None when all-time."""
        return None if self.month is None else next_month(self.month)

    @property
    def label(self):
        """For headings: 'July 2026', or 'All time'."""
        return "All time" if self.month is None else self.month.strftime("%B %Y")

    @property
    def param(self):
        """What to put in ?month= to get this filter back: 'all' or '2026-07'."""
        return ALL_TIME if self.month is None else self.month.strftime("%Y-%m")

    def apply(self, queryset, field="date"):
        """Narrow `queryset` to this month on `field`.

        A no-op when all-time, so callers filter unconditionally rather than
        branching around it at every call site.
        """
        if self.month is None:
            return queryset
        return queryset.filter(
            **{f"{field}__gte": self.start, f"{field}__lte": self.end}
        )


def get_month_filter(request, param="month"):
    """The month asked for in ?month=, defaulting to the current one.

    Accepts 'YYYY-MM', or 'all' for every month at once. Anything else falls
    back to the current month: the parameter arrives from bookmarks and
    hand-edited URLs as well as from the pager, so garbage should land the
    operator somewhere useful rather than raise — the same reasoning as
    `_parse_date` and `Paginator.get_page` in views.py.
    """
    raw = (request.GET.get(param) or "").strip().lower()

    if raw == ALL_TIME:
        return MonthFilter(month=None)

    if raw:
        try:
            year, month = raw.split("-")
            return MonthFilter(month=date(int(year), int(month), 1))
        except (ValueError, TypeError):
            # Unparsable, or well-formed but impossible like 2026-13.
            pass

    return MonthFilter(month=month_start(timezone.localdate()))
