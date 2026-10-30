from functools import wraps

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect

from .models import User
from .permissions import permission_for_url

DEFAULT_DENIED_MESSAGE = "You don't have permission to access that page."


def role_required(
    *roles,
    redirect_to="core:dashboard",
    message=DEFAULT_DENIED_MESSAGE,
    allow_permission=True,
):
    """Restrict a view to the given roles.

    Anonymous users go to the login page (they may just not be signed in yet).
    A signed-in user with the wrong role is bounced to `redirect_to` with an
    error flash — not a 403 — so they land somewhere useful.
    """

    def decorator(view_func):
        @wraps(view_func)
        @login_required
        def _wrapped(request, *args, **kwargs):
            role = getattr(request.user, "role", None)
            permission = permission_for_url(
                getattr(getattr(request, "resolver_match", None), "url_name", None)
            )
            allowed_by_permission = (
                allow_permission
                and permission
                and request.user.has_erp_permission(permission)
            )
            if role not in roles and not allowed_by_permission:
                messages.error(request, message)
                return redirect(redirect_to)
            return view_func(request, *args, **kwargs)

        return _wrapped

    return decorator


#: Views only a super admin may open.
super_admin_required = role_required(User.Role.SUPER_ADMIN, allow_permission=False)
