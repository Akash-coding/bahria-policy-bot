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
from scraper.extractors import pack_pages, unpack_pages
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

    def test_pdf_page_markers_round_trip(self):
        packed = pack_pages([(1, "Leave policy page one."), (2, "Leave policy page two.")])
        self.assertIn("[[page 1]]", packed)
        pages = unpack_pages(packed, "pdf")
        self.assertEqual(pages[0], (1, "Leave policy page one."))
        self.assertEqual(pages[1], (2, "Leave policy page two."))


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
            SCRAPE_DELAY=0,
            SCRAPE_MAX_DOCUMENTS=10,
            SCRAPE_MAX_IMAGES=10,
            GROQ_VISION_MODEL="off",
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

    def _asset_pages(self, url):
        url = url.replace("://www.", "://")
        notice_alt = (
            "Attendance notice: students must maintain seventy five percent attendance "
            "in every taught course during the semester."
        )
        pages = {
            "https://campus.example/robots.txt": FakeResponse(
                url, "User-agent: *\nAllow: /\n", content_type="text/plain"
            ),
            "https://campus.example/sitemap.xml": FakeResponse(url, "<xml></xml>", content_type="text/xml"),
            "https://campus.example/": FakeResponse(
                url,
                f"""
                <html><head><title>Campus Home</title></head>
                <body>
                  <a href="/admissions">Admissions</a>
                  <a href="/policy.pdf">Leave policy PDF</a>
                  <a href="/hostel.docx">Hostel rules</a>
                  <a href="/broken.pdf">Broken file</a>
                  <a href="https://files.external.test/secret.pdf">External</a>
                  <img src="/notice.png" alt="{notice_alt}">
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
            "https://campus.example/policy.pdf": FakeResponse(
                url, status_code=200, content_type="application/pdf", content=b"%PDF-fake-policy"
            ),
            "https://campus.example/hostel.docx": FakeResponse(
                url,
                status_code=200,
                content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                content=b"PK fake-docx",
            ),
            "https://campus.example/broken.pdf": FakeResponse(
                url, status_code=500, content_type="application/pdf", content=b"nope"
            ),
            "https://campus.example/notice.png": FakeResponse(
                url,
                status_code=200,
                content_type="image/png",
                content=b"\x89PNG" + b"x" * 1200,
            ),
        }
        return pages[url]

    def test_scrape_indexes_documents_and_images_without_stopping_on_failure(self):
        self.client.force_authenticate(self.admin)

        def fake_pages(payload, file_type, name="file"):
            if file_type == "pdf":
                return [(1, "Official leave policy allows ten days of casual leave each semester.")]
            if file_type == "docx":
                return [(None, "Hostel residents must return to campus housing by ten pm.")]
            raise RuntimeError(file_type)

        with patch("scraper.crawler._http_get", side_effect=self._asset_pages):
            with patch("scraper.crawler.pages_from_bytes", side_effect=fake_pages):
                response = self.client.post("/api/scraper/", {"url": "https://campus.example"}, format="json")
        self.assertIn(response.status_code, {200, 201}, response.content)
        website = WebsiteSource.objects.get(domain="campus.example")
        self.assertEqual(website.status, WebsiteStatus.COMPLETED)
        self.assertGreaterEqual(website.page_count, 2)
        self.assertGreaterEqual(website.document_count, 2)
        self.assertEqual(website.image_count, 1)
        self.assertGreaterEqual(website.failed_count, 1)
        self.assertGreater(website.chunk_count, 0)

        pdf = ScrapedPage.objects.get(website=website, url="https://campus.example/policy.pdf")
        self.assertEqual(pdf.status, PageStatus.COMPLETED)
        self.assertEqual(pdf.file_type, "pdf")
        self.assertIn("[[page 1]]", pdf.content)
        self.assertEqual(pdf.source_url, "https://campus.example/policy.pdf")

        image = ScrapedPage.objects.get(website=website, url="https://campus.example/notice.png")
        self.assertEqual(image.status, PageStatus.COMPLETED)
        self.assertEqual(image.file_type, "image")
        self.assertEqual(image.image_url, "https://campus.example/notice.png")
        self.assertIn("seventy five percent", image.content)

        failed = ScrapedPage.objects.get(website=website, url="https://campus.example/broken.pdf")
        self.assertEqual(failed.status, PageStatus.FAILED)
        self.assertIn("HTTP 500", failed.error_message)

        urls = set(website.pages.values_list("url", flat=True))
        self.assertFalse(any("external.test" in item for item in urls))

        payload = self.client.get("/api/scraper/item/", {"id": website.id}).json()
        self.assertGreaterEqual(payload["document_count"], 2)
        self.assertEqual(payload["image_count"], 1)
        self.assertGreaterEqual(payload["failed_count"], 1)

        self.assertEqual(_source_type_label({"source_type": "pdf", "source_url": pdf.source_url}), "PDF")
        self.assertEqual(_source_type_label({"source_type": "word", "source_url": "https://campus.example/hostel.docx"}), "Word Document")
        self.assertEqual(
            _source_type_label({"source_type": "image", "image_url": image.image_url, "source_url": image.source_url}),
            "Image",
        )
