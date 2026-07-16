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
