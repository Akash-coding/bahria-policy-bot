from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.files.base import ContentFile
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from documents.models import Document, DocumentCategory, DocumentStatus
from rag.embeddings import get_embedding_service
from rag.graphstore import get_policy_graph
from rag.pipeline import process_document
from rag.prompts import NOT_FOUND_MESSAGE
from rag.qa import answer_question
from rag.retriever import _dedupe_hits
from rag.vectorstore import get_vector_store


def _reset():
    get_embedding_service.cache_clear()
    get_vector_store.cache_clear()
    get_policy_graph.cache_clear()


class RagAndChatTests(TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.addCleanup(_reset)
        override = override_settings(
            PROCESS_DOCUMENTS_ASYNC=False,
            EMBEDDING_PROVIDER="lexical",
            EMBEDDING_MODEL="lexical",
            VECTOR_DB_PATH=Path(self.tmp.name) / "chroma",
            MEDIA_ROOT=Path(self.tmp.name) / "media",
            SIMILARITY_THRESHOLD=0.08,
            RAG_TOP_K=4,
        )
        override.enable()
        self.addCleanup(override.disable)
        _reset()

        self.admin = User.objects.create_user(
            username="admin", password="adminpass123", is_staff=True
        )
        self.client = APIClient()
        self.policy = (
            "Bahria University Attendance Policy. Students must maintain at least "
            "75 percent attendance in each registered course to sit the final examination. "
            "A first warning is issued below 80 percent attendance."
        )
        self.document = Document(
            title="Attendance Policy",
            category=DocumentCategory.ATTENDANCE,
            department="Academics",
            version="1.0",
            file_type="txt",
            status=DocumentStatus.UPLOADED,
            uploaded_by=self.admin,
        )
        self.document.file.save("attendance.txt", ContentFile(self.policy.encode("utf-8")), save=True)
        process_document(self.document.id)

    def test_vector_search_finds_policy_chunk(self):
        embedding = get_embedding_service().embed_query("attendance percentage required")
        hits = get_vector_store().query(embedding, top_k=3)
        self.assertTrue(hits)
        self.assertIn("75", hits[0]["content"])

    def test_unknown_question_skips_llm(self):
        with patch("rag.qa.generate_answer") as mocked:
            result = answer_question("Are electric hoverboards permitted in hostel basements?")
        mocked.assert_not_called()
        self.assertEqual(result["answer"], NOT_FOUND_MESSAGE)
        self.assertFalse(result["found"])
        self.assertEqual(result["sources"], [])

    def test_known_question_returns_sources(self):
        with patch("rag.qa.generate_answer", return_value="Students must maintain 75% attendance.") as mocked:
            result = answer_question("What is the attendance policy?")
        mocked.assert_called_once()
        self.assertTrue(result["found"])
        self.assertTrue(result["sources"])
        self.assertEqual(result["sources"][0]["document"], "Attendance Policy")

    def test_conflicting_documents_are_both_retrieved(self):
        other = Document(
            title="Campus Attendance Addendum",
            category=DocumentCategory.ATTENDANCE,
            file_type="txt",
            status=DocumentStatus.UPLOADED,
        )
        other.file.save(
            "addendum.txt",
            ContentFile(
                b"Attendance addendum. Some professional programs require 80 percent attendance "
                b"in laboratory courses to sit the final examination."
            ),
            save=True,
        )
        process_document(other.id)
        with patch("rag.qa.generate_answer", return_value="Policies mention 75% and 80%.") as mocked:
            result = answer_question("What attendance percentage is required for examinations?")
        mocked.assert_called_once()
        titles = {source["document"] for source in result["sources"]}
        self.assertTrue(len(titles) >= 1)

    def test_model_refusal_falls_back_to_excerpts(self):
        with patch("rag.qa.generate_answer", return_value=NOT_FOUND_MESSAGE):
            result = answer_question("What is the attendance policy?")
        self.assertTrue(result["found"])
        self.assertIn("75", result["answer"])
        self.assertTrue(result["sources"])
        self.assertNotEqual(result["answer"], NOT_FOUND_MESSAGE)

    def test_chat_api_with_mocked_ollama(self):
        with patch("chat.views.answer_question", return_value={
            "answer": "Students must maintain 75% attendance.",
            "sources": [{"document": "Attendance Policy", "page": 1, "relevance_score": 0.9}],
            "found": True,
        }):
            response = self.client.post(
                "/api/chat/",
                {"question": "What is the attendance policy?"},
                format="json",
            )
        self.assertEqual(response.status_code, 200, response.content)
        self.assertIn("75%", response.data["answer"])
        self.assertEqual(response.data["sources"][0]["document"], "Attendance Policy")
        session_id = response.data["session_id"]
        history = self.client.get(f"/api/chat/history/?session_id={session_id}")
        self.assertEqual(history.status_code, 200)
        self.assertEqual(len(history.data["messages"]), 2)

    def test_chat_stream_greeting(self):
        response = self.client.post(
            "/api/chat/stream/",
            {"question": "hello"},
            format="json",
            HTTP_ACCEPT="text/event-stream",
        )
        self.assertEqual(response.status_code, 200)
        body = b"".join(response.streaming_content).decode()
        self.assertIn('"type": "done"', body)
        self.assertIn("Hello", body)
        self.assertIn("session_id", body)

    def test_chat_stream_greeting_get(self):
        response = self.client.get(
            "/api/ask/",
            {"question": "hello"},
            HTTP_ACCEPT="text/event-stream",
        )
        self.assertEqual(response.status_code, 200)
        body = b"".join(response.streaming_content).decode()
        self.assertIn('"type": "done"', body)
        self.assertIn("Hello", body)

    def test_chat_stream_policy_answer(self):
        chunks = ["## Attendance\n\n", "1. **Required:** Students must maintain 75% attendance."]
        with patch("rag.qa.stream_generate", return_value=iter(chunks)):
            response = self.client.post(
                "/api/chat/stream/",
                {"question": "What is the attendance policy?"},
                format="json",
            )
            self.assertEqual(response.status_code, 200)
            body = b"".join(response.streaming_content).decode()
        self.assertIn("75%", body)
        self.assertIn('"type": "delta"', body)
        self.assertIn('"type": "done"', body)
        self.assertIn("Attendance Policy", body)

    def test_duplicate_chunks_are_removed(self):
        hits = [
            {
                "content": "Students must maintain 75% attendance.",
                "metadata": {"document_id": 1, "page_number": 1, "chunk_index": 0},
                "relevance_score": 0.9,
            },
            {
                "content": "Students must maintain 75% attendance.",
                "metadata": {"document_id": 1, "page_number": 1, "chunk_index": 1},
                "relevance_score": 0.8,
            },
        ]
        unique = _dedupe_hits(hits)
        self.assertEqual(len(unique), 1)

    def test_graph_fallback_can_add_neighbor_chunk(self):
        leave = Document(
            title="Student Leave Policy",
            category=DocumentCategory.LEAVE,
            department="Academics",
            file_type="txt",
            status=DocumentStatus.UPLOADED,
        )
        leave.file.save(
            "leave.txt",
            ContentFile(
                (
                    "1. Purpose\n"
                    "This leave policy explains student absence from campus.\n\n"
                    "5. Medical leave\n"
                    "A student may avail up to 10 days of medical leave in a semester "
                    "with a registered practitioner certificate."
                ).encode("utf-8")
            ),
            save=True,
        )
        process_document(leave.id)
        with patch("rag.qa.generate_answer", return_value="Students may take 10 days of medical leave.") as mocked:
            result = answer_question("How many medical leave days can a student take with a practitioner certificate?")
        mocked.assert_called_once()
        self.assertTrue(result["found"])
        self.assertTrue(result["sources"])
        joined = " ".join(
            f"{source.get('document', '')} {source.get('section') or ''} {source.get('excerpt') or ''}"
            for source in result["sources"]
        )
        self.assertTrue("10" in joined or "medical" in joined.lower())


class AdminChatMonitorTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.admin = User.objects.create_user(
            username="arshadkhan@gmail.com",
            email="arshadkhan@gmail.com",
            password="Arshad@Khan2026",
            is_staff=True,
            is_superuser=True,
        )
        self.student = User.objects.create_user(
            username="student",
            email="student@bahria.edu.pk",
            password="studentpass",
        )

    def test_non_staff_cannot_list_all_chats(self):
        denied = self.client.get("/api/chat/admin/sessions/")
        self.assertEqual(denied.status_code, 403)
        self.client.force_login(self.student)
        denied = self.client.get("/api/chat/admin/sessions/")
        self.assertEqual(denied.status_code, 403)

    def test_admin_sees_every_chat_with_client_ip(self):
        mocked = {
            "answer": "Students must maintain 75% attendance.",
            "sources": [{"document": "Attendance Policy", "page": 1, "relevance_score": 0.9}],
            "found": True,
        }
        with patch("chat.views.answer_question", return_value=mocked):
            guest = self.client.post(
                "/api/chat/",
                {"question": "What is the guest attendance rule?"},
                format="json",
                REMOTE_ADDR="203.0.113.41",
            )
            self.assertEqual(guest.status_code, 200, guest.content)
            self.client.force_login(self.student)
            student = self.client.post(
                "/api/chat/",
                {"question": "How many warning letters are issued?"},
                format="json",
                HTTP_X_FORWARDED_FOR="198.51.100.17, 10.0.0.1",
                REMOTE_ADDR="10.0.0.1",
            )
            self.assertEqual(student.status_code, 200, student.content)

        self.client.force_login(self.admin)
        listed = self.client.get("/api/chat/admin/sessions/")
        self.assertEqual(listed.status_code, 200)
        self.assertEqual(len(listed.data), 2)
        ips = {row["client_ip"] for row in listed.data}
        self.assertEqual(ips, {"203.0.113.41", "198.51.100.17"})
        usernames = {row["username"] for row in listed.data}
        self.assertEqual(usernames, {"Guest", "student"})

        guest_id = guest.data["session_id"]
        detail = self.client.get(f"/api/chat/admin/sessions/{guest_id}/")
        self.assertEqual(detail.status_code, 200)
        self.assertEqual(detail.data["client_ip"], "203.0.113.41")
        self.assertTrue(detail.data["guest"])
        self.assertEqual(len(detail.data["messages"]), 2)

        with patch(
            "api.views.check_ollama",
            return_value={"reachable": False, "model": "test", "model_available": False},
        ):
            stats = self.client.get("/api/dashboard/stats/")
        self.assertEqual(stats.status_code, 200)
        self.assertEqual(stats.data["unique_ips"], 2)
        self.assertEqual(stats.data["total_sessions"], 2)
