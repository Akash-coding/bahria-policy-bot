from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from rest_framework.test import APIClient


class AuthApiTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.admin = User.objects.create_user(
            username="admin", password="adminpass123", is_staff=True, is_superuser=True
        )
        self.user = User.objects.create_user(username="student", password="studentpass")

    def test_csrf_endpoint_sets_cookie(self):
        response = self.client.get("/api/auth/csrf/")
        self.assertEqual(response.status_code, 200)
        self.assertIn("csrftoken", response.cookies)

    def test_login_success_and_me(self):
        response = self.client.post(
            "/api/auth/login/",
            {"username": "admin", "password": "adminpass123"},
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.data["is_staff"])
        me = self.client.get("/api/auth/me/")
        self.assertTrue(me.data["authenticated"])
        self.assertEqual(me.data["user"]["username"], "admin")

    def test_login_failure(self):
        response = self.client.post(
            "/api/auth/login/",
            {"username": "admin", "password": "wrong"},
            format="json",
        )
        self.assertEqual(response.status_code, 401)

    def test_logout(self):
        self.client.login(username="admin", password="adminpass123")
        response = self.client.post("/api/auth/logout/")
        self.assertEqual(response.status_code, 200)
        me = self.client.get("/api/auth/me/")
        self.assertFalse(me.data["authenticated"])

    def test_logout_after_json_login_with_csrf(self):
        csrf = self.client.get("/api/auth/csrf/")
        token = csrf.cookies["csrftoken"].value
        login = self.client.post(
            "/api/auth/login/",
            {"username": "admin", "password": "adminpass123"},
            format="json",
            HTTP_X_CSRFTOKEN=token,
        )
        self.assertEqual(login.status_code, 200)
        token = self.client.cookies["csrftoken"].value
        response = self.client.post(
            "/api/auth/logout/",
            format="json",
            HTTP_X_CSRFTOKEN=token,
        )
        self.assertEqual(response.status_code, 200)
        me = self.client.get("/api/auth/me/")
        self.assertFalse(me.data["authenticated"])

    def test_login_accepts_email(self):
        User.objects.create_user(
            username="arshadkhan@gmail.com",
            email="arshadkhan@gmail.com",
            password="Arshad@Khan2026",
            is_staff=True,
            is_superuser=True,
        )
        response = self.client.post(
            "/api/auth/login/",
            {"username": "arshadkhan@gmail.com", "password": "Arshad@Khan2026"},
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["username"], "arshadkhan@gmail.com")
        self.assertTrue(response.data["is_staff"])
