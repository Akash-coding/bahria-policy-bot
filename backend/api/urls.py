from django.urls import include, path

from . import views

urlpatterns = [
    path("health/", views.health, name="health"),
    path("dashboard/stats/", views.dashboard_stats, name="dashboard-stats"),
    path("auth/", include("accounts.urls")),
    path("documents/", include("documents.urls")),
    path("chat/", include("chat.urls")),
]
