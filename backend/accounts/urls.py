from django.urls import path

from . import views

urlpatterns = [
    path("csrf/", views.csrf_token, name="auth-csrf"),
    path("login/", views.login_view, name="auth-login"),
    path("logout/", views.logout_view, name="auth-logout"),
    path("me/", views.me_view, name="auth-me"),
]
