from __future__ import annotations

import logging
import threading

from django.conf import settings

from .crawler import crawl_website
from .indexer import index_website
from .models import WebsiteSource, WebsiteStatus

logger = logging.getLogger("scraper")


def enqueue_website_scrape(website_id: int) -> None:
    if settings.PROCESS_DOCUMENTS_ASYNC:
        thread = threading.Thread(
            target=_run_scrape,
            args=(website_id,),
            daemon=True,
            name=f"scrape-website-{website_id}",
        )
        thread.start()
        logger.info("Queued website scrape %s", website_id)
        return
    _run_scrape(website_id)


def _run_scrape(website_id: int) -> None:
    try:
        crawl_website(website_id)
        index_website(website_id)
    except Exception as exc:
        logger.exception("Website scrape failed for %s", website_id)
        WebsiteSource.objects.filter(pk=website_id).update(
            status=WebsiteStatus.FAILED,
            error_message=str(exc),
        )
