from django.http import JsonResponse
from django.shortcuts import render
from django.utils.deprecation import MiddlewareMixin

from .permissions import permission_for_url


class ERPPermissionMiddleware(MiddlewareMixin):
    """Enforce the registry at the resolved URL, including direct POST/AJAX calls."""

    def process_view(self, request, view_func, view_args, view_kwargs):
        match = getattr(request, "resolver_match", None)
        url_name = getattr(match, "url_name", None)
        permission = permission_for_url(url_name)
        user = getattr(request, "user", None)
        public_names = {"landing", "notification_dismiss", "profile"}
        admin_only_prefixes = (
            "/users/",
            "/settings/user-management/",
            "/audit-log/",
            "/db-console/",
        )
        if (
            user
            and user.is_authenticated
            and getattr(user, "role", None) == "manager"
            and url_name not in public_names
            and not request.path.startswith(admin_only_prefixes)
            and (not permission or not user.has_erp_permission(permission))
        ):
            if request.path.startswith("/api/") or request.headers.get("Accept") == "application/json":
                return JsonResponse({"detail": "You do not have permission for this operation."}, status=403)
            return render(request, "403.html", status=403)
        if (
            user
            and user.is_authenticated
            and getattr(user, "role", None) == "manager"
            and request.path.startswith(admin_only_prefixes)
        ):
            return JsonResponse(
                {"detail": "Manager accounts cannot access administration tools."},
                status=403,
            ) if request.path.startswith("/api/") else render(request, "403.html", status=403)
        return None
