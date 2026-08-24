from django.conf import settings
from django.db.models import Count
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

from accounts.permissions import IsStaffUser
from chat.models import ChatMessage, ChatSession, MessageRole
from documents.models import Document, DocumentStatus
from rag.ollama_client import check_ollama
from rag.vectorstore import get_vector_store


@api_view(["GET"])
@permission_classes([AllowAny])
def health(_request):
    ollama = check_ollama()
    return Response(
        {
            "status": "ok",
            "service": "bahria-policy-bot",
            "ollama": ollama,
            "embedding_provider": settings.EMBEDDING_PROVIDER,
            "embedding_model": settings.EMBEDDING_MODEL,
            "llm_model": settings.OLLAMA_MODEL,
        }
    )


@api_view(["GET"])
@permission_classes([IsStaffUser])
def dashboard_stats(_request):
    documents = Document.objects.all()
    by_status = {
        row["status"]: row["total"]
        for row in documents.values("status").annotate(total=Count("id"))
    }
    try:
        vector_count = get_vector_store().count()
    except Exception:
        vector_count = 0

    return Response(
        {
            "total_documents": documents.count(),
            "processed_documents": by_status.get(DocumentStatus.COMPLETED, 0),
            "processing_documents": by_status.get(DocumentStatus.PROCESSING, 0)
            + by_status.get(DocumentStatus.UPLOADED, 0),
            "failed_documents": by_status.get(DocumentStatus.FAILED, 0),
            "uploaded_documents": by_status.get(DocumentStatus.UPLOADED, 0),
            "total_queries": ChatMessage.objects.filter(role=MessageRole.USER).count(),
            "total_sessions": ChatSession.objects.count(),
            "unique_ips": len(
                {
                    ip
                    for ip in ChatSession.objects.values_list("client_ip", flat=True)
                    if ip
                }
            ),
            "indexed_chunks": vector_count,
            "ollama": check_ollama(),
        }
    )
