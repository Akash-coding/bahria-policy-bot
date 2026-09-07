from __future__ import annotations

from urllib.parse import urlparse

from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.response import Response

from accounts.permissions import IsStaffUser

from .crawler import host_key, normalize_url
from .indexer import index_website, remove_website_from_index
from .models import WebsiteSource, WebsiteStatus
from .serializers import WebsiteListSerializer, WebsiteSourceSerializer
from .tasks import enqueue_website_scrape


def _validated_url(raw: str) -> str:
    url = normalize_url(raw or "")
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("Enter a valid website URL, for example https://example.com")
    return url


@api_view(["GET", "POST"])
@permission_classes([IsStaffUser])
def website_list_create(request):
    if request.method == "GET":
        rows = WebsiteSource.objects.all()
        return Response(WebsiteListSerializer(rows, many=True).data)

    try:
        seed = _validated_url(request.data.get("url") or request.query_params.get("url") or "")
    except ValueError as exc:
        return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)

    domain = host_key(urlparse(seed).netloc)
    website, created = WebsiteSource.objects.get_or_create(
        domain=domain,
        defaults={
            "seed_url": seed,
            "status": WebsiteStatus.STARTING,
            "created_by": request.user,
        },
    )
    if not created:
        website.seed_url = seed
        website.status = WebsiteStatus.STARTING
        website.error_message = ""
        website.progress_detail = "Starting"
        website.save(
            update_fields=["seed_url", "status", "error_message", "progress_detail", "updated_at"]
        )
    enqueue_website_scrape(website.id)
    website.refresh_from_db()
    return Response(
        WebsiteListSerializer(website).data,
        status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
    )


@api_view(["GET", "POST", "DELETE"])
@permission_classes([IsStaffUser])
def website_item(request):
    website_id = request.query_params.get("id") or request.data.get("id")
    try:
        website = WebsiteSource.objects.get(pk=website_id)
    except (WebsiteSource.DoesNotExist, ValueError, TypeError):
        return Response({"detail": "Website not found."}, status=status.HTTP_404_NOT_FOUND)

    if request.method == "GET":
        return Response(WebsiteSourceSerializer(website).data)

    if request.method == "DELETE":
        remove_website_from_index(website.id)
        website.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)

    action = (request.query_params.get("action") or request.data.get("action") or "rescrape").lower()
    if action == "reindex":
        website.status = WebsiteStatus.PROCESSING
        website.save(update_fields=["status", "updated_at"])
        try:
            index_website(website.id)
        except Exception as exc:
            website.status = WebsiteStatus.FAILED
            website.error_message = str(exc)
            website.save(update_fields=["status", "error_message", "updated_at"])
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        website.refresh_from_db()
        return Response(WebsiteSourceSerializer(website).data)

    website.status = WebsiteStatus.STARTING
    website.progress_detail = "Starting"
    website.error_message = ""
    website.save(update_fields=["status", "progress_detail", "error_message", "updated_at"])
    enqueue_website_scrape(website.id)
    website.refresh_from_db()
    return Response(WebsiteSourceSerializer(website).data)
