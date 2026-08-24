from django.contrib import admin

from .models import Document, DocumentChunk


@admin.register(Document)
class DocumentAdmin(admin.ModelAdmin):
    list_display = (
        "title",
        "category",
        "department",
        "version",
        "status",
        "chunk_count",
        "uploaded_by",
        "created_at",
    )
    list_filter = ("category", "status", "department")
    search_fields = ("title", "description", "department")
    readonly_fields = ("status", "error_message", "chunk_count", "file_type", "created_at", "updated_at")


@admin.register(DocumentChunk)
class DocumentChunkAdmin(admin.ModelAdmin):
    list_display = ("document", "chunk_index", "page_number", "created_at")
    search_fields = ("content", "document__title")
    readonly_fields = ("vector_id",)
