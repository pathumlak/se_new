import re
from django import template

register = template.Library()


@register.filter
def clean_product_name(value):
    if not value:
        return value

    return re.sub(r"\s*-\s*[^-]+$", "", value).strip()