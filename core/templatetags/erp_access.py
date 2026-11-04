from django import template
from core.permissions import can_access
register = template.Library()


@register.filter
def can_task(user, route):
    return can_access(user, route)


class PermittedNode(template.Node):
    def __init__(self, route, method, nodes):
        self.route, self.method, self.nodes = route, method, nodes

    def render(self, context):
        request = context.get("request")
        if request and any(can_access(request.user, route, self.method) for route in self.route.split("|")):
            return self.nodes.render(context)
        if self.method == "POST":
            from django.utils.safestring import mark_safe
            return mark_safe('<fieldset disabled hidden style="display:none">' + self.nodes.render(context) + '</fieldset>')
        # Preserve modal opener IDs expected by existing JavaScript.
        rendered = self.nodes.render(context)
        if rendered.lstrip().startswith("<button"):
            from django.utils.safestring import mark_safe
            return mark_safe('<span hidden style="display:none">' + rendered.replace('<button', '<button disabled', 1) + '</span>')
        return ""


@register.tag
def permitted(parser, token):
    bits = token.split_contents()
    route = bits[1].strip("\"'")
    method = bits[2].strip("\"'") if len(bits) > 2 else "GET"
    nodes = parser.parse(("endpermitted",))
    parser.delete_first_token()
    return PermittedNode(route, method, nodes)
