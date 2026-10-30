from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.urls.resolvers import URLPattern, URLResolver

from .models import (
    PermissionAssignment,
    PermissionAudit,
    PermissionDefinition,
    PermissionTemplate,
    User,
)
from .permissions import URL_PERMISSION_MAP


@override_settings(ALLOWED_HOSTS=["testserver"])
class ManagerPermissionTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user(
            username="admin", password="password", role=User.Role.SUPER_ADMIN
        )
        self.manager = User.objects.create_user(
            username="manager", password="password", role=User.Role.MANAGER
        )
        self.client = Client()
        self.client.login(username="manager", password="password")

    def grant(self, codename):
        permission = PermissionDefinition.objects.get(codename=codename)
        return PermissionAssignment.objects.create(
            user=self.manager, permission=permission, granted_by=self.admin
        )

    def test_manager_is_default_deny_and_grant_is_immediate(self):
        self.assertEqual(self.client.get("/products/").status_code, 403)
        self.grant("products.view")
        self.assertEqual(self.client.get("/products/").status_code, 200)

    def test_revocation_protects_direct_json_endpoint(self):
        self.assertEqual(self.client.post("/api/customer-price/save-all/").status_code, 403)

    def test_super_admin_can_manage_manager_permissions(self):
        self.client.login(username="admin", password="password")
        response = self.client.post(
            reverse("core:manager_permissions_for_user", kwargs={"user_pk": self.manager.pk}),
            {"permissions": ["products.view", "reports.view"]},
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            self.manager.erp_permission_assignments.count(), 2
        )
        self.assertEqual(PermissionAudit.objects.filter(target_user=self.manager).count(), 2)

    def test_template_copy_does_not_remove_existing_grants(self):
        self.grant("products.view")
        template = PermissionTemplate.objects.create(
            name="Sales", created_by=self.admin
        )
        template.permissions.add(PermissionDefinition.objects.get(codename="reports.view"))
        self.client.login(username="admin", password="password")
        response = self.client.post(
            reverse("core:permission_template_assign", kwargs={"template_pk": template.pk}),
            {"manager": self.manager.pk},
        )
        self.assertEqual(response.status_code, 302)
        self.assertSetEqual(
            set(self.manager.erp_permission_assignments.values_list("permission__codename", flat=True)),
            {"products.view", "reports.view"},
        )

    def test_manager_cannot_reach_administration_even_if_granted(self):
        self.grant("user_management.manage")
        self.assertEqual(self.client.get("/users/").status_code, 403)
        self.assertEqual(self.client.get("/db-console/").status_code, 403)

    def test_all_core_business_routes_have_permission_mapping(self):
        names = []

        def walk(patterns):
            for pattern in patterns:
                if isinstance(pattern, URLPattern):
                    if pattern.name:
                        names.append(pattern.name)
                elif isinstance(pattern, URLResolver):
                    walk(pattern.url_patterns)

        from .urls import urlpatterns

        walk(urlpatterns)
        excluded = {
            "landing", "notification_dismiss", "profile", "login", "logout",
            "password_change", "password_change_done", "autocomplete", "jsi18n",
        }
        missing = {
            name for name in names
            if name not in excluded
            and name not in URL_PERMISSION_MAP
        }
        self.assertFalse(missing, f"Unmapped routes: {sorted(missing)}")
