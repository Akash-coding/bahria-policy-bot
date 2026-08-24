from rest_framework import serializers

from .models import Document, DocumentChunk, DocumentStatus


class DocumentSerializer(serializers.ModelSerializer):
    uploaded_by_username = serializers.CharField(
        source="uploaded_by.username", read_only=True, default=None
    )
    file_url = serializers.SerializerMethodField()
    category_label = serializers.CharField(source="get_category_display", read_only=True)
    status_label = serializers.CharField(source="get_status_display", read_only=True)

    class Meta:
        model = Document
        fields = [
            "id",
            "title",
            "category",
            "category_label",
            "department",
            "description",
            "file",
            "file_url",
            "file_name",
            "file_type",
            "version",
            "status",
            "status_label",
            "error_message",
            "chunk_count",
            "uploaded_by",
            "uploaded_by_username",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "file_type",
            "status",
            "error_message",
            "chunk_count",
            "uploaded_by",
            "created_at",
            "updated_at",
        ]

    def get_file_url(self, obj):
        request = self.context.get("request")
        if obj.file and request:
            return request.build_absolute_uri(obj.file.url)
        return None


class DocumentUploadSerializer(serializers.ModelSerializer):
    class Meta:
        model = Document
        fields = [
            "title",
            "category",
            "department",
            "description",
            "file",
            "version",
        ]

    def validate_title(self, value: str) -> str:
        value = (value or "").strip()
        if not value:
            raise serializers.ValidationError("Title is required.")
        return value


class DocumentChunkSerializer(serializers.ModelSerializer):
    class Meta:
        model = DocumentChunk
        fields = ["id", "chunk_index", "page_number", "content", "created_at"]


class DocumentDetailSerializer(DocumentSerializer):
    chunks = serializers.SerializerMethodField()

    class Meta(DocumentSerializer.Meta):
        fields = DocumentSerializer.Meta.fields + ["chunks"]

    def get_chunks(self, obj):
        preview = obj.chunks.all()[:20]
        return DocumentChunkSerializer(preview, many=True).data


class DocumentStatusSerializer(serializers.ModelSerializer):
    class Meta:
        model = Document
        fields = ["id", "status", "error_message", "chunk_count", "updated_at"]
        extra_kwargs = {"status": {"read_only": True}}


# Keep DocumentStatus imported for type checkers / admin usage.
_ = DocumentStatus
