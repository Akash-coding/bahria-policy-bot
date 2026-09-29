from __future__ import annotations

import logging
from typing import Any

from django.conf import settings
from django.utils import timezone

from rag.chunking import split_pages
from rag.embeddings import get_embedding_service
from rag.vectorstore import get_vector_store

from .extractors import DOCUMENT_TYPES, source_type_for, unpack_pages
from .models import PageStatus, ScrapedPage, WebsiteSource, WebsiteStatus

logger = logging.getLogger("scraper")


def index_website(website_id: int) -> WebsiteSource:
    website = WebsiteSource.objects.get(pk=website_id)
    website.status = WebsiteStatus.PROCESSING
    website.progress_detail = "Splitting and embedding pages, documents, and images"
    website.save(update_fields=["status", "progress_detail", "updated_at"])

    remove_website_from_index(website.id, rebuild_graph=False)
    total_chunks = 0
    pages = list(website.pages.filter(status=PageStatus.COMPLETED).exclude(content=""))
    for index, page in enumerate(pages, start=1):
        website.progress_detail = f"Indexing {index}/{len(pages)} sources"
        website.save(update_fields=["progress_detail", "updated_at"])
        total_chunks += _index_page(page)

    html_count = website.pages.filter(status=PageStatus.COMPLETED, file_type="html").count()
    document_count = website.pages.filter(
        status=PageStatus.COMPLETED, file_type__in=list(DOCUMENT_TYPES)
    ).count()
    image_count = website.pages.filter(status=PageStatus.COMPLETED, file_type="image").count()
    failed_count = website.pages.filter(status=PageStatus.FAILED).count()
    website.chunk_count = total_chunks
    website.page_count = html_count
    website.document_count = document_count
    website.image_count = image_count
    website.failed_count = failed_count
    website.status = WebsiteStatus.SAVING
    website.progress_detail = "Rebuilding search index"
    website.save(
        update_fields=[
            "chunk_count",
            "page_count",
            "document_count",
            "image_count",
            "failed_count",
            "status",
            "progress_detail",
            "updated_at",
        ]
    )

    from rag.retriever import rebuild_graph_from_store

    rebuild_graph_from_store()
    website.status = WebsiteStatus.COMPLETED
    website.progress_detail = (
        f"{html_count} pages, {document_count} documents, {image_count} images, {total_chunks} chunks"
    )
    website.error_message = ""
    website.save(update_fields=["status", "progress_detail", "error_message", "updated_at"])
    logger.info("Indexed website %s with %s chunks", website.domain, total_chunks)
    return website


def remove_website_from_index(website_id: int, rebuild_graph: bool = True) -> None:
    get_vector_store().delete_matching(website_id=website_id)
    ScrapedPage.objects.filter(website_id=website_id).update(chunk_count=0)
    if not rebuild_graph:
        return
    from rag.retriever import rebuild_graph_from_store

    try:
        rebuild_graph_from_store()
    except Exception:
        logger.exception("Graph rebuild failed after removing website %s", website_id)


def _index_page(page: ScrapedPage) -> int:
    pages = unpack_pages(page.content, page.file_type)
    if not pages:
        pages = [(page.page_number, page.content)]
    chunks = split_pages(
        pages,
        chunk_size=settings.CHUNK_SIZE,
        overlap=settings.CHUNK_OVERLAP,
    )
    if not chunks:
        page.chunk_count = 0
        page.save(update_fields=["chunk_count"])
        return 0

    embeddings = get_embedding_service().embed_texts([chunk.content for chunk in chunks])
    ids: list[str] = []
    documents: list[str] = []
    metadatas: list[dict[str, Any]] = []
    source_type = source_type_for(page.file_type)
    extracted_at = ""
    if page.scraped_at:
        extracted_at = page.scraped_at.isoformat()
    else:
        extracted_at = timezone.now().isoformat()
    image_url = page.image_url or (page.url if page.file_type == "image" else "")
    for chunk in chunks:
        vector_id = f"web-{page.id}-chunk-{chunk.chunk_index}"
        metadata = {
            "document_id": None,
            "website_id": page.website_id,
            "page_id": page.id,
            "document_title": page.title or page.website.domain,
            "category": "website",
            "department": "",
            "version": "",
            "page_number": chunk.page_number if chunk.page_number is not None else (page.page_number or -1),
            "chunk_index": chunk.chunk_index,
            "section": chunk.section or "",
            "file_type": page.file_type,
            "source_type": source_type,
            "source_url": page.source_url or page.url,
            "image_url": image_url,
            "extracted_at": extracted_at,
        }
        ids.append(vector_id)
        documents.append(chunk.content)
        metadatas.append(metadata)

    get_vector_store().upsert_chunks(
        ids=ids,
        embeddings=embeddings,
        documents=documents,
        metadatas=metadatas,
    )
    page.chunk_count = len(chunks)
    page.save(update_fields=["chunk_count"])
    return len(chunks)
