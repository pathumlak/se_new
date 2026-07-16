from django import template

register = template.Library()


@register.filter
def elided_page_range(page_obj):
    """The page numbers to draw, with Paginator.ELLIPSIS standing in for gaps.

    A filter rather than something the view passes down, so the partial needs
    nothing but the page_obj it is handed — any Page from any view renders the
    same pager.

    on_each_side/on_ends are tightened from Django's defaults, which run to 3
    and 2 and give a pager wide enough to wrap on a narrow screen. One either
    side of the current page plus the first and last is the
    `Previous | 1 … 4 5 6 … 20 | Next` shape.
    """
    return page_obj.paginator.get_elided_page_range(
        page_obj.number, on_each_side=1, on_ends=1
    )
