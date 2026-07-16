from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse


class DashboardAccessTests(TestCase):
    def test_dashboard_redirects_anonymous_to_login(self):
        response = self.client.get(reverse("core:dashboard"))
        self.assertRedirects(
            response, f"{reverse('login')}?next={reverse('core:dashboard')}"
        )

    def test_dashboard_renders_for_logged_in_user(self):
        get_user_model().objects.create_user(username="tester", password="pw-for-tests")
        self.client.login(username="tester", password="pw-for-tests")
        response = self.client.get(reverse("core:dashboard"))
        self.assertEqual(response.status_code, 200)
