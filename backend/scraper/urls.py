from django.urls import path

from . import views

urlpatterns = [
    path("", views.website_list_create, name="scraper-sites"),
    path("item/", views.website_item, name="scraper-item"),
]
