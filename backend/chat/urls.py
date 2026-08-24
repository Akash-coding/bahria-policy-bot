from django.urls import path

from . import views

urlpatterns = [
    path("", views.chat_ask, name="chat-ask"),
    path("stream/", views.chat_ask_stream, name="chat-ask-stream"),
    path("history/", views.chat_history, name="chat-history"),
    path("sessions/", views.session_list_create, name="chat-sessions"),
    path("sessions/<uuid:pk>/", views.session_detail, name="chat-session-detail"),
    path("admin/sessions/", views.admin_session_list, name="admin-chat-sessions"),
    path("admin/sessions/<uuid:pk>/", views.admin_session_detail, name="admin-chat-session-detail"),
]
