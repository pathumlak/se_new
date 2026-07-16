"""URL configuration for senovka_erp project.

Auth is login/logout only — there is deliberately no registration, password
reset or password change route. Accounts are provisioned via `manage.py seed`
or the Django admin.
"""

from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.urls import include, path

urlpatterns = [
    path("admin/", admin.site.urls),
    path(
        "login/",
        auth_views.LoginView.as_view(
            template_name="registration/login.html",
            redirect_authenticated_user=True,
        ),
        name="login",
    ),
    # LogoutView is POST-only since Django 4.1; base.html posts a CSRF form.
    path("logout/", auth_views.LogoutView.as_view(), name="logout"),
    path("", include("core.urls")),
]
