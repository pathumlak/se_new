from django.contrib.auth.decorators import login_required
from django.shortcuts import render


@login_required
def dashboard(request):
    """Landing page after login."""
    return render(request, "core/dashboard.html")
