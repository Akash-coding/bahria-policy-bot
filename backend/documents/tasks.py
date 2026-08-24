from __future__ import annotations

import logging
import threading

from django.conf import settings

from rag.pipeline import process_document

logger = logging.getLogger("documents")


def enqueue_document_processing(document_id: int) -> None:
    if settings.PROCESS_DOCUMENTS_ASYNC:
        thread = threading.Thread(
            target=_run_processing,
            args=(document_id,),
            daemon=True,
            name=f"process-document-{document_id}",
        )
        thread.start()
        logger.info("Queued background processing for document %s", document_id)
        return
    process_document(document_id)


def _run_processing(document_id: int) -> None:
    try:
        process_document(document_id)
    except Exception:
        logger.exception("Background processing failed for document %s", document_id)
