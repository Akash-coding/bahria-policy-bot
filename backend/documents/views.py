from django.core.exceptions import ValidationError as DjangoValidationError
from django.db.models import Q
from rest_framework import status
from rest_framework.decorators import api_view, parser_classes, permission_classes
from rest_framework.exceptions import ValidationError
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.response import Response

from accounts.permissions import IsStaffUser
from rag.pipeline import ProcessingError, process_document, remove_document_from_index

from .models import Document, DocumentCategory, DocumentStatus
from .serializers import (
    DocumentDetailSerializer,
    DocumentSerializer,
    DocumentUploadSerializer,
)
from .tasks import enqueue_document_processing
from .validators import validate_uploaded_policy_file


@api_view(["GET", "POST"])
@permission_classes([IsStaffUser])
@parser_classes([MultiPartParser, FormParser])
def document_list_create(request):
    if request.method == "GET":
        queryset = Document.objects.select_related("uploaded_by").all()
        search = request.query_params.get("search", "").strip()
        category = request.query_params.get("category", "").strip()
        status_filter = request.query_params.get("status", "").strip()
        if search:
            queryset = queryset.filter(
                Q(title__icontains=search)
                | Q(description__icontains=search)
                | Q(department__icontains=search)
            )
        if category:
            queryset = queryset.filter(category=category)
        if status_filter:
            queryset = queryset.filter(status=status_filter)
        serializer = DocumentSerializer(queryset, many=True, context={"request": request})
        return Response(serializer.data)

    upload = DocumentUploadSerializer(data=request.data)
    upload.is_valid(raise_exception=True)
    uploaded_file = upload.validated_data["file"]
    try:
        file_type = validate_uploaded_policy_file(uploaded_file)
    except DjangoValidationError as exc:
        raise ValidationError({"file": exc.messages}) from exc
    document = upload.save(
        uploaded_by=request.user,
        file_type=file_type,
        status=DocumentStatus.UPLOADED,
    )
    enqueue_document_processing(document.id)
    document.refresh_from_db()
    return Response(
        DocumentSerializer(document, context={"request": request}).data,
        status=status.HTTP_201_CREATED,
    )


@api_view(["GET", "DELETE"])
@permission_classes([IsStaffUser])
def document_detail(request, pk: int):
    try:
        document = Document.objects.select_related("uploaded_by").get(pk=pk)
    except Document.DoesNotExist:
        return Response({"detail": "Document not found."}, status=status.HTTP_404_NOT_FOUND)

    if request.method == "GET":
        return Response(DocumentDetailSerializer(document, context={"request": request}).data)

    try:
        remove_document_from_index(document.id)
    except Exception:
        pass
    if document.file:
        document.file.delete(save=False)
    document.delete()
    return Response(status=status.HTTP_204_NO_CONTENT)


@api_view(["POST"])
@permission_classes([IsStaffUser])
def document_reprocess(request, pk: int):
    try:
        document = Document.objects.get(pk=pk)
    except Document.DoesNotExist:
        return Response({"detail": "Document not found."}, status=status.HTTP_404_NOT_FOUND)

    document.status = DocumentStatus.UPLOADED
    document.error_message = ""
    document.save(update_fields=["status", "error_message", "updated_at"])

    sync = str(request.query_params.get("sync", "")).lower() in {"1", "true", "yes"}
    if sync:
        try:
            process_document(document.id)
        except ProcessingError as exc:
            document.refresh_from_db()
            return Response(
                {
                    "detail": str(exc),
                    "document": DocumentSerializer(document, context={"request": request}).data,
                },
                status=status.HTTP_400_BAD_REQUEST,
            )
        document.refresh_from_db()
        return Response(DocumentSerializer(document, context={"request": request}).data)

    enqueue_document_processing(document.id)
    document.refresh_from_db()
    return Response(DocumentSerializer(document, context={"request": request}).data)


@api_view(["GET"])
@permission_classes([IsStaffUser])
def document_categories(_request):
    return Response(
        [{"value": value, "label": label} for value, label in DocumentCategory.choices]
    )
