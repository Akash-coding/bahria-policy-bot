from django.conf import settings
from django.db.models import Count
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

from accounts.permissions import IsStaffUser
from chat.models import ChatMessage, ChatSession, MessageRole
from documents.models import Document, DocumentStatus
from rag.embeddings import embedding_status
from rag.groq_client import check_groq
from rag.vectorstore import get_vector_store


@api_view(["GET"])
@permission_classes([AllowAny])
def health(_request):
    groq = check_groq()
    embeddings = embedding_status()
    try:
        store = get_vector_store()
        indexed_chunks = store.count()
        sample = store.all_items()[:1]
        vector_dim = len((sample[0].get("embedding") or []) if sample else [])
    except Exception:
        indexed_chunks = 0
        vector_dim = 0
    return Response(
        {
            "status": "ok",
            "service": "bahria-policy-bot",
            "groq": groq,
            "embedding_provider": embeddings.get("active_provider") or settings.EMBEDDING_PROVIDER,
            "embedding_model": embeddings.get("active_model") or settings.EMBEDDING_MODEL,
            "embedding_source": embeddings.get("source") or "",
            "embedding_local_path": embeddings.get("local_model_path") or "",
            "llm_model": settings.GROQ_MODEL,
            "indexed_chunks": indexed_chunks,
            "vector_dim": vector_dim,
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
            "groq": check_groq(),
        }
    )
