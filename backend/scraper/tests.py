from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from rag.embeddings import get_embedding_service
from rag.graphstore import get_policy_graph
from rag.qa import _source_type_label, _unique_sources
from rag.vectorstore import get_vector_store
from scraper.crawler import host_key, normalize_url, same_domain
from scraper.models import PageStatus, ScrapedPage, WebsiteSource, WebsiteStatus


def _reset():
    get_embedding_service.cache_clear()
    get_vector_store.cache_clear()
    get_policy_graph.cache_clear()


class FakeResponse:
    def __init__(self, url, text="", status_code=200, content_type="text/html", content=b""):
        self.url = url
        self.text = text
        self.status_code = status_code
        self.content = content or text.encode("utf-8")
        self.headers = {"content-type": content_type}


class CrawlerHelperTests(TestCase):
    def test_same_domain_ignores_www_and_external_links(self):
        self.assertEqual(host_key("www.bahria.edu.pk"), "bahria.edu.pk")
        self.assertTrue(same_domain("https://www.bahria.edu.pk/admissions", "bahria.edu.pk"))
        self.assertFalse(same_domain("https://other.edu.pk/admissions", "bahria.edu.pk"))
        self.assertEqual(
            normalize_url("https://Example.com/Admissions/"),
            "https://example.com/Admissions",
        )


class ScraperPipelineTests(TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.addCleanup(_reset)
        override = override_settings(
            PROCESS_DOCUMENTS_ASYNC=False,
            EMBEDDING_PROVIDER="lexical",
            EMBEDDING_MODEL="lexical",
            VECTOR_DB_PATH=Path(self.tmp.name) / "chroma",
            SCRAPE_MAX_PAGES=10,
            SCRAPE_MAX_DEPTH=2,
        )
        override.enable()
        self.addCleanup(override.disable)
        _reset()
        self.admin = User.objects.create_user(
            username="admin", password="adminpass123", is_staff=True
        )
        self.client = APIClient()

    def _pages(self, url):
        url = url.replace("://www.", "://")
        pages = {
            "https://campus.example/robots.txt": FakeResponse(
                url, "User-agent: *\nAllow: /\n", content_type="text/plain"
            ),
            "https://campus.example/sitemap.xml": FakeResponse(url, "<xml></xml>", content_type="text/xml"),
            "https://campus.example/": FakeResponse(
                url,
                """
                <html><head><title>Campus Home</title></head>
                <body>
                  <a href="/admissions">Admissions</a>
                  <a href="https://external.test/nope">External</a>
                  <p>Welcome to the campus admissions information portal for students.</p>
                </body></html>
                """,
            ),
            "https://campus.example/admissions": FakeResponse(
                url,
                """
                <html><head><title>Admissions</title></head>
                <body>
                  <p>Undergraduate admissions require 50 percent marks in intermediate exams.</p>
                </body></html>
                """,
            ),
        }
        return pages[url]

    def test_staff_can_scrape_and_index_internal_pages(self):
        self.client.force_authenticate(self.admin)
        with patch("scraper.crawler._http_get", side_effect=self._pages):
            response = self.client.post("/api/scraper/", {"url": "https://campus.example"}, format="json")
        self.assertIn(response.status_code, {200, 201}, response.content)
        website = WebsiteSource.objects.get(domain="campus.example")
        self.assertEqual(website.status, WebsiteStatus.COMPLETED)
        urls = set(website.pages.filter(status=PageStatus.COMPLETED).values_list("url", flat=True))
        self.assertIn("https://campus.example/", urls)
        self.assertIn("https://campus.example/admissions", urls)
        self.assertFalse(any("external.test" in item for item in urls))
        self.assertGreater(website.chunk_count, 0)

        hits = [
            {
                "content": "Undergraduate admissions require 50 percent marks.",
                "metadata": {
                    "document_title": "Admissions",
                    "source_type": "website",
                    "source_url": "https://campus.example/admissions",
                    "page_number": -1,
                    "chunk_index": 0,
                },
                "relevance_score": 0.9,
            }
        ]
        sources = _unique_sources(hits)
        self.assertEqual(sources[0]["source_type"], "Website")
        self.assertEqual(sources[0]["source_url"], "https://campus.example/admissions")
        self.assertEqual(_source_type_label(hits[0]["metadata"]), "Website")

    def test_non_staff_cannot_start_scrape(self):
        denied = self.client.post("/api/scraper/", {"url": "https://campus.example"}, format="json")
        self.assertEqual(denied.status_code, 403)

    def test_duplicate_url_updates_same_website(self):
        self.client.force_authenticate(self.admin)
        with patch("scraper.crawler._http_get", side_effect=self._pages):
            first = self.client.post("/api/scraper/", {"url": "https://campus.example"}, format="json")
            second = self.client.post(
                "/api/scraper/",
                {"url": "https://www.campus.example/admissions"},
                format="json",
            )
        self.assertEqual(first.status_code, 201)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(WebsiteSource.objects.count(), 1)
        self.assertGreaterEqual(ScrapedPage.objects.filter(status=PageStatus.COMPLETED).count(), 1)
