from django.http import JsonResponse
from django.shortcuts import render, redirect
from django.utils.deprecation import MiddlewareMixin
from .permissions import can_access


class ERPPermissionMiddleware(MiddlewareMixin):
    def process_view(self, request, view_func, view_args, view_kwargs):
        match = request.resolver_match
        user = request.user
        if not user.is_authenticated or user.role == "super_admin":
            return None
        route = match.url_name
        # A manager with no dashboard grant still has a useful landing page.
        if route == "dashboard" and not can_access(user, route):
            return redirect("core:profile")
        if not can_access(user, route, request.method):
            if request.content_type == "application/json" or "application/json" in request.headers.get("Accept", "") or request.path.startswith("/api/"):
                return JsonResponse({"detail": "You do not have permission for this operation."}, status=403)
            return render(request, "403.html", status=403)
        return None
