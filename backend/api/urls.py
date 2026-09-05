from django.urls import include, path

from chat.views import chat_ask_stream

from . import views

urlpatterns = [
    path("health/", views.health, name="health"),
    path("ask/", chat_ask_stream, name="policy-ask"),
    path("dashboard/stats/", views.dashboard_stats, name="dashboard-stats"),
    path("auth/", include("accounts.urls")),
    path("documents/", include("documents.urls")),
    path("chat/", include("chat.urls")),
]
