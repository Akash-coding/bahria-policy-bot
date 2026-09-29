from __future__ import annotations

import hashlib
import logging
import re
import time
from collections import deque
from html.parser import HTMLParser
from typing import Callable
from urllib.parse import urljoin, urlparse, urlunparse
from urllib.robotparser import RobotFileParser

import requests
from django.conf import settings
from django.utils import timezone

from rag.extraction import ExtractionError, normalize_policy_text

from .extractors import (
    DOCUMENT_TYPES,
    file_type_of,
    image_text,
    is_decorative_image,
    is_document_url,
    is_image_url,
    pack_pages,
    pages_from_bytes,
)
from .models import PageStatus, ScrapedPage, WebsiteSource, WebsiteStatus

logger = logging.getLogger("scraper")

USER_AGENT = "BahriaPolicyBot/1.0 (+https://bahria.edu.pk)"
SKIP_EXTENSIONS = {
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
_SPACE = re.compile(r"\s+")
_SRCSET_FIRST = re.compile(r"^\s*([^\s,]+)")


class _LinkParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.hrefs: list[str] = []
        self.images: list[tuple[str, str]] = []
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
        if tag in {"a", "area"}:
            href = attrs_map.get("href", "").strip()
            if href:
                self.hrefs.append(href)
        if tag in {"iframe", "embed"}:
            src = attrs_map.get("src", "").strip()
            if src:
                self.hrefs.append(src)
        if tag == "object":
            data = attrs_map.get("data", "").strip()
            if data:
                self.hrefs.append(data)
        if tag == "img":
            alt = attrs_map.get("alt", "").strip()
            for key in ("src", "data-src", "data-original", "data-lazy-src"):
                src = attrs_map.get(key, "").strip()
                if src:
                    self.images.append((src, alt))
            srcset = attrs_map.get("srcset", "").strip()
            if srcset:
                first = _first_srcset(srcset)
                if first:
                    self.images.append((first, alt))
        if tag == "source":
            srcset = attrs_map.get("srcset", "").strip() or attrs_map.get("src", "").strip()
            if srcset:
                first = _first_srcset(srcset)
                if first:
                    self.images.append((first, ""))
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


def _first_srcset(value: str) -> str:
    match = _SRCSET_FIRST.search(value or "")
    return match.group(1) if match else ""


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
    lowered = (url or "").lower()
    if lowered.startswith(("javascript:", "mailto:", "tel:", "data:")):
        return True
    path = parsed.path.lower()
    for ext in SKIP_EXTENSIONS:
        if path.endswith(ext):
            return True
    return False


class ScrapeFetchError(RuntimeError):
    pass


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


def _response_text(response: requests.Response) -> str:
    payload = response.content or b""
    if not payload:
        return ""
    for encoding in ("utf-8", "utf-8-sig"):
        try:
            return payload.decode(encoding)
        except UnicodeDecodeError:
            continue
    declared = response.encoding or "cp1252"
    try:
        return payload.decode(declared)
    except (LookupError, UnicodeDecodeError):
        return payload.decode("utf-8", errors="replace")


def _http_get(url: str) -> requests.Response:
    timeout = getattr(settings, "SCRAPE_TIMEOUT", 20)
    max_bytes = int(getattr(settings, "SCRAPE_MAX_FILE_MB", 12) * 1024 * 1024)
    response = requests.get(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": (
                "text/html,application/pdf,application/msword,"
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document,"
                "image/jpeg,image/png,image/webp,image/gif,text/plain,*/*;q=0.8"
            ),
        },
        timeout=timeout,
        allow_redirects=True,
        stream=True,
    )
    chunks: list[bytes] = []
    total = 0
    for piece in response.iter_content(chunk_size=65536):
        if not piece:
            continue
        total += len(piece)
        if total > max_bytes:
            response.close()
            raise ScrapeFetchError(f"File larger than {max_bytes // (1024 * 1024)} MB")
        chunks.append(piece)
    response._content = b"".join(chunks)
    response._content_consumed = True
    return response


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
    max_docs = getattr(settings, "SCRAPE_MAX_DOCUMENTS", 40)
    max_images = getattr(settings, "SCRAPE_MAX_IMAGES", 25)
    delay = float(getattr(settings, "SCRAPE_DELAY", 0.12) or 0)
    seed = normalize_url(website.seed_url)
    seed_host = urlparse(seed).netloc
    website.domain = host_key(seed_host)
    website.error_message = ""
    website.pages.all().delete()
    from .indexer import remove_website_from_index

    remove_website_from_index(website.id, rebuild_graph=False)
    _set_progress(website, WebsiteStatus.STARTING, "Checking robots.txt")

    robots = _load_robots(seed, fetch)
    _set_progress(website, WebsiteStatus.DISCOVERING, "Finding pages, documents, and images")

    queued: deque[tuple[str, str, int]] = deque([(seed, "", 0)])
    seen: set[str] = {seed}
    image_alts: dict[str, str] = {}
    for extra in _discover_sitemap(seed, fetch):
        normalized = normalize_url(extra)
        if normalized not in seen and same_domain(normalized, seed_host) and not _is_skippable(normalized):
            seen.add(normalized)
            queued.append((normalized, seed, 1))

    hashes: set[str] = set()
    html_saved = 0
    docs_saved = 0
    images_saved = 0
    _sync_counts(website)
    _set_progress(website, WebsiteStatus.SCRAPING, _progress_line(website, len(queued)))

    while queued:
        if html_saved >= max_pages and docs_saved >= max_docs and images_saved >= max_images:
            break
        url, parent, depth = queued.popleft()
        guessed = file_type_of(url)
        if guessed in DOCUMENT_TYPES and docs_saved >= max_docs:
            continue
        if guessed == "image" and images_saved >= max_images:
            continue
        if guessed not in DOCUMENT_TYPES and guessed != "image" and html_saved >= max_pages:
            continue
        if not _allowed_by_robots(robots, url):
            continue
        try:
            response = fetch(url)
            if delay:
                time.sleep(delay)
            content_type = response.headers.get("content-type") or ""
            if response.status_code >= 400:
                kind = file_type_of(url, content_type) or guessed
                status = PageStatus.FAILED if kind in DOCUMENT_TYPES or kind == "image" else PageStatus.SKIPPED
                _save_page(
                    website,
                    url,
                    parent,
                    file_type=kind or "html",
                    status=status,
                    error=f"HTTP {response.status_code}",
                    image_url=url if kind == "image" else "",
                )
                _sync_counts(website)
                _set_progress(website, WebsiteStatus.SCRAPING, _progress_line(website, len(queued)))
                continue
            final_url = normalize_url(response.url or url)
            if not same_domain(final_url, seed_host):
                continue
            kind = file_type_of(final_url, content_type) or guessed
            payload = response.content or b""

            if kind in DOCUMENT_TYPES:
                if docs_saved >= max_docs:
                    continue
                saved = _ingest_document(
                    website,
                    final_url,
                    parent,
                    payload,
                    kind,
                    hashes,
                )
                if saved:
                    docs_saved += 1
            elif kind == "image":
                if images_saved >= max_images:
                    continue
                saved = _ingest_image(
                    website,
                    final_url,
                    parent,
                    payload,
                    content_type,
                    image_alts.get(final_url) or image_alts.get(url, ""),
                    hashes,
                )
                if saved:
                    images_saved += 1
            elif "html" in content_type.lower() or not content_type:
                if html_saved >= max_pages:
                    continue
                parser = _LinkParser()
                parser.feed(_response_text(response))
                title = normalize_policy_text(parser.title or website.domain)
                text = normalize_policy_text(parser.text())
                digest = hashlib.sha256((text or "").encode("utf-8")).hexdigest()
                if not text or len(text) < 40:
                    _save_page(
                        website,
                        final_url,
                        parent,
                        title=title,
                        file_type="html",
                        status=PageStatus.SKIPPED,
                        error="Too little text",
                    )
                elif digest in hashes:
                    _save_page(
                        website,
                        final_url,
                        parent,
                        title=title,
                        content=text,
                        digest=digest,
                        file_type="html",
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
                        file_type="html",
                        status=PageStatus.COMPLETED,
                    )
                    html_saved += 1
                    if not website.title and title:
                        website.title = title[:255]
                if depth < max_depth:
                    _enqueue_links(
                        parser,
                        final_url,
                        seed_host,
                        depth,
                        seen,
                        queued,
                        image_alts,
                    )
            else:
                _save_page(
                    website,
                    final_url,
                    parent,
                    file_type=kind or "html",
                    status=PageStatus.SKIPPED,
                    error="Unsupported content type",
                )
            _sync_counts(website)
            _set_progress(website, WebsiteStatus.SCRAPING, _progress_line(website, len(queued)))
        except Exception as exc:
            logger.warning("Skipped %s: %s", url, exc)
            kind = file_type_of(url) or "html"
            _save_page(
                website,
                url,
                parent,
                file_type=kind,
                status=PageStatus.FAILED,
                error=str(exc)[:500],
                image_url=url if kind == "image" else "",
            )
            _sync_counts(website)
            _set_progress(website, WebsiteStatus.SCRAPING, _progress_line(website, len(queued)))

    _sync_counts(website)
    website.last_scraped_at = timezone.now()
    if not website.title:
        website.title = website.domain
    website.save(update_fields=["title", "last_scraped_at", "updated_at"])


def _enqueue_links(
    parser: _LinkParser,
    final_url: str,
    seed_host: str,
    depth: int,
    seen: set[str],
    queued: deque[tuple[str, str, int]],
    image_alts: dict[str, str],
) -> None:
    next_depth = depth + 1
    for href in parser.hrefs:
        absolute = _safe_absolute(final_url, href)
        if not absolute or absolute in seen or _is_skippable(absolute) or not same_domain(absolute, seed_host):
            continue
        seen.add(absolute)
        if is_document_url(absolute) or is_image_url(absolute):
            queued.appendleft((absolute, final_url, next_depth))
        else:
            queued.append((absolute, final_url, next_depth))
    for src, alt in parser.images:
        absolute = _safe_absolute(final_url, src)
        if not absolute or _is_skippable(absolute) or not same_domain(absolute, seed_host):
            continue
        if is_decorative_image(absolute, alt):
            continue
        if alt and len(alt) > len(image_alts.get(absolute, "")):
            image_alts[absolute] = alt
        if absolute in seen:
            continue
        seen.add(absolute)
        queued.appendleft((absolute, final_url, next_depth))


def _safe_absolute(base: str, href: str) -> str:
    raw = (href or "").strip()
    if not raw or raw.startswith(("#", "javascript:", "mailto:", "tel:", "data:")):
        return ""
    try:
        return normalize_url(urljoin(base, raw))
    except Exception:
        return ""


def _ingest_document(
    website: WebsiteSource,
    url: str,
    parent: str,
    payload: bytes,
    file_type: str,
    hashes: set[str],
) -> bool:
    try:
        pages = pages_from_bytes(payload, file_type, name=url)
        text = pack_pages(pages)
    except ExtractionError as exc:
        _save_page(
            website,
            url,
            parent,
            file_type=file_type,
            status=PageStatus.FAILED,
            error=str(exc)[:500],
        )
        return False
    except Exception as exc:
        logger.warning("Document extract failed for %s: %s", url, exc)
        _save_page(
            website,
            url,
            parent,
            file_type=file_type,
            status=PageStatus.FAILED,
            error=str(exc)[:500],
        )
        return False
    digest = hashlib.sha256((text or "").encode("utf-8")).hexdigest()
    title = urlparse(url).path.rsplit("/", 1)[-1] or website.domain
    if not text or len(text) < 40:
        _save_page(
            website,
            url,
            parent,
            title=title,
            file_type=file_type,
            status=PageStatus.SKIPPED,
            error="Too little text",
        )
        return False
    if digest in hashes:
        _save_page(
            website,
            url,
            parent,
            title=title,
            content=text,
            digest=digest,
            file_type=file_type,
            status=PageStatus.SKIPPED,
            error="Duplicate content",
        )
        return False
    hashes.add(digest)
    first_page = pages[0][0] if pages else None
    _save_page(
        website,
        url,
        parent,
        title=title,
        content=text,
        digest=digest,
        file_type=file_type,
        status=PageStatus.COMPLETED,
        page_number=first_page,
    )
    if not website.title:
        website.title = title[:255]
    return True


def _ingest_image(
    website: WebsiteSource,
    url: str,
    parent: str,
    payload: bytes,
    content_type: str,
    alt: str,
    hashes: set[str],
) -> bool:
    if is_decorative_image(url, alt):
        _save_page(
            website,
            url,
            parent,
            file_type="image",
            status=PageStatus.SKIPPED,
            error="Decorative image skipped",
            image_url=url,
        )
        return False
    if len(payload or b"") < 800 and len((alt or "").strip()) < 20:
        _save_page(
            website,
            url,
            parent,
            file_type="image",
            status=PageStatus.SKIPPED,
            error="Image too small",
            image_url=url,
        )
        return False
    mime = (content_type or "image/jpeg").split(";")[0].strip()
    try:
        text = image_text(payload, mime=mime, alt=alt, source_url=url)
    except Exception as exc:
        logger.warning("Image extract failed for %s: %s", url, exc)
        _save_page(
            website,
            url,
            parent,
            file_type="image",
            status=PageStatus.FAILED,
            error=str(exc)[:500],
            image_url=url,
        )
        return False
    digest = hashlib.sha256((payload or b"") + (text or "").encode("utf-8")).hexdigest()
    title = (alt[:120] if alt else urlparse(url).path.rsplit("/", 1)[-1]) or website.domain
    if not text or len(text) < 40:
        _save_page(
            website,
            url,
            parent,
            title=title,
            file_type="image",
            status=PageStatus.SKIPPED,
            error="No useful text in image",
            image_url=url,
        )
        return False
    if digest in hashes:
        _save_page(
            website,
            url,
            parent,
            title=title,
            content=text,
            digest=digest,
            file_type="image",
            status=PageStatus.SKIPPED,
            error="Duplicate image",
            image_url=url,
        )
        return False
    hashes.add(digest)
    _save_page(
        website,
        url,
        parent,
        title=title,
        content=text,
        digest=digest,
        file_type="image",
        status=PageStatus.COMPLETED,
        image_url=url,
    )
    return True


def _progress_line(website: WebsiteSource, queued_n: int) -> str:
    return (
        f"{website.page_count} pages · {website.document_count} documents · "
        f"{website.image_count} images · {website.failed_count} failed · {queued_n} queued"
    )


def _sync_counts(website: WebsiteSource) -> None:
    pages = website.pages.all()
    website.page_count = pages.filter(status=PageStatus.COMPLETED, file_type="html").count()
    website.document_count = pages.filter(status=PageStatus.COMPLETED, file_type__in=list(DOCUMENT_TYPES)).count()
    website.image_count = pages.filter(status=PageStatus.COMPLETED, file_type="image").count()
    website.failed_count = pages.filter(status=PageStatus.FAILED).count()
    website.save(
        update_fields=["page_count", "document_count", "image_count", "failed_count", "updated_at"]
    )


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
    image_url: str = "",
    page_number: int | None = None,
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
            "image_url": (image_url or "")[:800],
            "page_number": page_number,
            "file_type": file_type or "html",
            "status": status,
            "error_message": error,
        },
    )
    return page
