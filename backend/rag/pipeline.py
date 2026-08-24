from __future__ import annotations

import logging
from typing import Any

from django.conf import settings
from django.db import transaction

from documents.models import Document, DocumentChunk, DocumentStatus

from .chunking import split_pages
from .embeddings import EmbeddingError, get_embedding_service
from .extraction import ExtractionError, extract_pages
from .vectorstore import VectorStoreError, get_vector_store

logger = logging.getLogger("rag")


class ProcessingError(RuntimeError):
    pass


def process_document(document_id: int) -> Document:
    document = Document.objects.get(pk=document_id)
    logger.info("Processing document %s (%s)", document.id, document.title)
    document.status = DocumentStatus.PROCESSING
    document.error_message = ""
    document.save(update_fields=["status", "error_message", "updated_at"])

    try:
        _index_document(document)
        document.status = DocumentStatus.COMPLETED
        document.error_message = ""
        document.save(update_fields=["status", "error_message", "chunk_count", "updated_at"])
        logger.info(
            "Completed document %s with %s chunks", document.id, document.chunk_count
        )
        return document
    except Exception as exc:
        logger.exception("Failed to process document %s", document.id)
        document.status = DocumentStatus.FAILED
        document.error_message = str(exc)
        document.chunk_count = 0
        document.save(update_fields=["status", "error_message", "chunk_count", "updated_at"])
        raise ProcessingError(str(exc)) from exc


def remove_document_from_index(document_id: int) -> None:
    store = get_vector_store()
    try:
        store.delete_document(document_id)
    except VectorStoreError:
        logger.exception("Vector delete failed for document %s", document_id)
        raise
    DocumentChunk.objects.filter(document_id=document_id).delete()
    from .retriever import rebuild_graph_from_store

    try:
        rebuild_graph_from_store()
    except Exception:
        logger.exception("Policy graph rebuild failed after deleting document %s", document_id)


def _index_document(document: Document) -> None:
    if not document.file:
        raise ProcessingError("Document has no file attached.")

    pages = extract_pages(document.file.path, document.file_type)
    chunks = split_pages(
        pages,
        chunk_size=settings.CHUNK_SIZE,
        overlap=settings.CHUNK_OVERLAP,
    )
    if not chunks:
        raise ProcessingError("No text chunks could be created from this document.")

    embeddings = get_embedding_service().embed_texts([chunk.content for chunk in chunks])
    if len(embeddings) != len(chunks):
        raise EmbeddingError("Embedding count did not match chunk count.")

    remove_document_from_index(document.id)

    records: list[DocumentChunk] = []
    ids: list[str] = []
    documents: list[str] = []
    metadatas: list[dict[str, Any]] = []

    for chunk, embedding in zip(chunks, embeddings):
        vector_id = f"doc-{document.id}-chunk-{chunk.chunk_index}"
        metadata = {
            "document_id": document.id,
            "document_title": document.title,
            "category": document.category,
            "department": document.department or "",
            "version": document.version or "",
            "page_number": chunk.page_number if chunk.page_number is not None else -1,
            "chunk_index": chunk.chunk_index,
            "section": chunk.section or "",
            "file_type": document.file_type,
        }
        records.append(
            DocumentChunk(
                document=document,
                content=chunk.content,
                page_number=chunk.page_number,
                chunk_index=chunk.chunk_index,
                vector_id=vector_id,
                metadata=metadata,
            )
        )
        ids.append(vector_id)
        documents.append(chunk.content)
        metadatas.append(metadata)
        _ = embedding  # embeddings used below

    store = get_vector_store()
    store.upsert_chunks(
        ids=ids,
        embeddings=embeddings,
        documents=documents,
        metadatas=metadatas,
    )

    with transaction.atomic():
        DocumentChunk.objects.bulk_create(records)
        document.chunk_count = len(records)
        document.save(update_fields=["chunk_count", "updated_at"])

    from .retriever import rebuild_graph_from_store

    rebuild_graph_from_store()
