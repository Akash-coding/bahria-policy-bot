from __future__ import annotations

import hashlib
import logging
import re
from collections import deque
from html.parser import HTMLParser
from typing import Callable
from urllib.parse import urljoin, urlparse, urlunparse
from urllib.robotparser import RobotFileParser

import requests
from django.conf import settings
from django.utils import timezone

from rag.extraction import extract_pages, ExtractionError

from .models import PageStatus, ScrapedPage, WebsiteSource, WebsiteStatus

logger = logging.getLogger("scraper")

USER_AGENT = "BahriaPolicyBot/1.0 (+https://bahria.edu.pk)"
SKIP_EXTENSIONS = {
    ".jpg",
    ".jpeg",
    ".png",
    ".gif",
    ".webp",
    ".svg",
    ".ico",
    ".css",
    ".js",
    ".map",
    ".woff",
    ".woff2",
    ".ttf",
    ".eot",
    ".mp4",
    ".mp3",
    ".zip",
    ".rar",
    ".exe",
    ".dmg",
}
PDF_EXTENSIONS = {".pdf"}
_SPACE = re.compile(r"\s+")


class _LinkParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.hrefs: list[str] = []
        self.title = ""
        self._capture_title = False
        self._skip_depth = 0
        self._chunks: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attrs_map = {key: value or "" for key, value in attrs}
        if tag in {"script", "style", "noscript", "svg"}:
            self._skip_depth += 1
            return
        if tag == "title":
            self._capture_title = True
        if tag == "a":
            href = attrs_map.get("href", "").strip()
            if href:
                self.hrefs.append(href)
        if tag in {"br", "p", "div", "li", "h1", "h2", "h3", "tr"}:
            self._chunks.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style", "noscript", "svg"} and self._skip_depth:
            self._skip_depth -= 1
        if tag == "title":
            self._capture_title = False

    def handle_data(self, data: str) -> None:
        text = data.strip()
        if not text or self._skip_depth:
            return
        if self._capture_title:
            self.title = (self.title + " " + text).strip()
            return
        self._chunks.append(text + " ")

    def text(self) -> str:
        return _SPACE.sub(" ", " ".join(self._chunks)).strip()


def host_key(host: str) -> str:
    host = (host or "").lower().split(":")[0]
    return host[4:] if host.startswith("www.") else host


def normalize_url(url: str) -> str:
    parsed = urlparse((url or "").strip())
    scheme = parsed.scheme.lower() if parsed.scheme in {"http", "https"} else "https"
    netloc = parsed.netloc.lower()
    path = parsed.path or "/"
    if path != "/" and path.endswith("/"):
        path = path.rstrip("/")
    return urlunparse((scheme, netloc, path, "", parsed.query, ""))


def same_domain(url: str, seed_host: str) -> bool:
    return host_key(urlparse(url).netloc) == host_key(seed_host)


def _is_skippable(url: str) -> bool:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        return True
    path = parsed.path.lower()
    for ext in SKIP_EXTENSIONS:
        if path.endswith(ext):
            return True
    return False


def _is_pdf(url: str, content_type: str = "") -> bool:
    path = urlparse(url).path.lower()
    return path.endswith(".pdf") or "application/pdf" in (content_type or "").lower()


def crawl_website(website_id: int, fetch: Callable[[str], requests.Response] | None = None) -> WebsiteSource:
    website = WebsiteSource.objects.get(pk=website_id)
    try:
        _crawl(website, fetch or _http_get)
    except Exception as exc:
        logger.exception("Website crawl failed for %s", website.seed_url)
        website.status = WebsiteStatus.FAILED
        website.error_message = str(exc)
        website.save(update_fields=["status", "error_message", "updated_at"])
        raise
    return website


def _http_get(url: str) -> requests.Response:
    timeout = getattr(settings, "SCRAPE_TIMEOUT", 20)
    return requests.get(
        url,
        headers={"User-Agent": USER_AGENT, "Accept": "text/html,application/pdf;q=0.9,*/*;q=0.8"},
        timeout=timeout,
        allow_redirects=True,
    )


def _set_progress(website: WebsiteSource, status: str, detail: str = "") -> None:
    website.status = status
    website.progress_detail = detail[:255]
    website.save(update_fields=["status", "progress_detail", "updated_at"])


def _allowed_by_robots(robots: RobotFileParser | None, url: str) -> bool:
    if robots is None:
        return True
    try:
        return robots.can_fetch(USER_AGENT, url)
    except Exception:
        return True


def _load_robots(seed: str, fetch: Callable[[str], requests.Response]) -> RobotFileParser | None:
    parsed = urlparse(seed)
    robots_url = urlunparse((parsed.scheme, parsed.netloc, "/robots.txt", "", "", ""))
    parser = RobotFileParser()
    try:
        response = fetch(robots_url)
        if response.status_code >= 400:
            return None
        parser.parse(response.text.splitlines())
        return parser
    except Exception:
        logger.info("Could not read robots.txt for %s", seed)
        return None


def _discover_sitemap(seed: str, fetch: Callable[[str], requests.Response]) -> list[str]:
    parsed = urlparse(seed)
    sitemap_url = urlunparse((parsed.scheme, parsed.netloc, "/sitemap.xml", "", "", ""))
    found: list[str] = []
    try:
        response = fetch(sitemap_url)
        if response.status_code >= 400 or "xml" not in (response.headers.get("content-type") or ""):
            if "<url>" not in (response.text or "") and "<loc>" not in (response.text or ""):
                return []
        for match in re.findall(r"<loc>\s*([^<]+)\s*</loc>", response.text, flags=re.I):
            found.append(match.strip())
    except Exception:
        return []
    return found


def _crawl(website: WebsiteSource, fetch: Callable[[str], requests.Response]) -> None:
    max_pages = getattr(settings, "SCRAPE_MAX_PAGES", 80)
    max_depth = getattr(settings, "SCRAPE_MAX_DEPTH", 5)
    seed = normalize_url(website.seed_url)
    seed_host = urlparse(seed).netloc
    website.domain = host_key(seed_host)
    website.error_message = ""
    website.pages.all().delete()
    from .indexer import remove_website_from_index

    remove_website_from_index(website.id, rebuild_graph=False)
    _set_progress(website, WebsiteStatus.STARTING, "Checking robots.txt")

    robots = _load_robots(seed, fetch)
    _set_progress(website, WebsiteStatus.DISCOVERING, "Finding internal pages")

    queued: deque[tuple[str, str, int]] = deque([(seed, "", 0)])
    seen: set[str] = {seed}
    for extra in _discover_sitemap(seed, fetch):
        normalized = normalize_url(extra)
        if normalized not in seen and same_domain(normalized, seed_host) and not _is_skippable(normalized):
            seen.add(normalized)
            queued.append((normalized, seed, 1))

    hashes: set[str] = set()
    scraped = 0
    _set_progress(website, WebsiteStatus.SCRAPING, f"0/{max_pages} pages")

    while queued and scraped < max_pages:
        url, parent, depth = queued.popleft()
        if not _allowed_by_robots(robots, url):
            continue
        try:
            response = fetch(url)
            if response.status_code >= 400:
                _save_page(website, url, parent, status=PageStatus.SKIPPED, error=f"HTTP {response.status_code}")
                continue
            content_type = response.headers.get("content-type") or ""
            final_url = normalize_url(response.url or url)
            if not same_domain(final_url, seed_host):
                continue
            if _is_pdf(final_url, content_type):
                title, text = _pdf_text(response.content)
                file_type = "pdf"
                links: list[str] = []
            elif "html" in content_type.lower() or not content_type:
                parser = _LinkParser()
                parser.feed(response.text or "")
                title = parser.title or website.domain
                text = parser.text()
                file_type = "html"
                links = parser.hrefs
            else:
                _save_page(website, final_url, parent, status=PageStatus.SKIPPED, error="Unsupported content type")
                continue

            digest = hashlib.sha256((text or "").encode("utf-8")).hexdigest()
            if not text or len(text) < 40:
                _save_page(website, final_url, parent, title=title, status=PageStatus.SKIPPED, error="Too little text")
            elif digest in hashes:
                _save_page(
                    website,
                    final_url,
                    parent,
                    title=title,
                    content=text,
                    digest=digest,
                    file_type=file_type,
                    status=PageStatus.SKIPPED,
                    error="Duplicate content",
                )
            else:
                hashes.add(digest)
                _save_page(
                    website,
                    final_url,
                    parent,
                    title=title,
                    content=text,
                    digest=digest,
                    file_type=file_type,
                    status=PageStatus.COMPLETED,
                )
                scraped += 1
                if not website.title and title:
                    website.title = title[:255]
            if depth < max_depth:
                for href in links:
                    absolute = normalize_url(urljoin(final_url, href))
                    if absolute in seen or _is_skippable(absolute) or not same_domain(absolute, seed_host):
                        continue
                    seen.add(absolute)
                    queued.append((absolute, final_url, depth + 1))
            _set_progress(website, WebsiteStatus.SCRAPING, f"{scraped} pages saved, {len(queued)} queued")
        except Exception as exc:
            logger.warning("Skipped %s: %s", url, exc)
            _save_page(website, url, parent, status=PageStatus.FAILED, error=str(exc)[:500])

    website.page_count = website.pages.filter(status=PageStatus.COMPLETED).count()
    website.last_scraped_at = timezone.now()
    if not website.title:
        website.title = website.domain
    website.save(update_fields=["title", "page_count", "last_scraped_at", "updated_at"])


def _save_page(
    website: WebsiteSource,
    url: str,
    parent: str,
    title: str = "",
    content: str = "",
    digest: str = "",
    file_type: str = "html",
    status: str = PageStatus.COMPLETED,
    error: str = "",
) -> ScrapedPage:
    page, _created = ScrapedPage.objects.update_or_create(
        website=website,
        url=url[:800],
        defaults={
            "title": (title or "")[:500],
            "content": content,
            "content_hash": digest,
            "source_url": url[:800],
            "parent_url": (parent or "")[:800],
            "file_type": file_type,
            "status": status,
            "error_message": error,
        },
    )
    return page


def _pdf_text(payload: bytes) -> tuple[str, str]:
    from tempfile import NamedTemporaryFile

    with NamedTemporaryFile(suffix=".pdf", delete=True) as handle:
        handle.write(payload)
        handle.flush()
        try:
            pages = extract_pages(handle.name, "pdf")
        except ExtractionError:
            return "", ""
    text = "\n\n".join(body for _page, body in pages if body)
    return "", text
