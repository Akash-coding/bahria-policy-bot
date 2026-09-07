from rest_framework import serializers

from .models import ScrapedPage, WebsiteSource


class ScrapedPageSerializer(serializers.ModelSerializer):
    class Meta:
        model = ScrapedPage
        fields = [
            "id",
            "url",
            "title",
            "source_url",
            "file_type",
            "status",
            "chunk_count",
            "content_hash",
            "scraped_at",
            "error_message",
        ]


class WebsiteSourceSerializer(serializers.ModelSerializer):
    status_label = serializers.CharField(source="get_status_display", read_only=True)
    pages = ScrapedPageSerializer(many=True, read_only=True)

    class Meta:
        model = WebsiteSource
        fields = [
            "id",
            "seed_url",
            "domain",
            "title",
            "status",
            "status_label",
            "progress_detail",
            "page_count",
            "chunk_count",
            "error_message",
            "last_scraped_at",
            "created_at",
            "updated_at",
            "pages",
        ]


class WebsiteListSerializer(WebsiteSourceSerializer):
    class Meta(WebsiteSourceSerializer.Meta):
        fields = [field for field in WebsiteSourceSerializer.Meta.fields if field != "pages"]
