from django.urls import path

from . import views

urlpatterns = [
    path("", views.document_list_create, name="document-list"),
    path("categories/", views.document_categories, name="document-categories"),
    path("<int:pk>/", views.document_detail, name="document-detail"),
    path("<int:pk>/reprocess/", views.document_reprocess, name="document-reprocess"),
]
