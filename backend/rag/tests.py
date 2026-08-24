from rag.chunking import split_pages
from rag.extraction import extract_pages
from rag.prompts import NOT_FOUND_MESSAGE
from rag.qa import _greeting_reply, _partial_visible, answer_question, sanitize_answer, stream_answer_events
from django.test import SimpleTestCase
from pathlib import Path
from tempfile import NamedTemporaryFile


class ChunkingTests(SimpleTestCase):
    def test_short_text_is_single_chunk(self):
        chunks = split_pages([(1, "Short policy text.")], chunk_size=100, overlap=20)
        self.assertEqual(len(chunks), 1)
        self.assertEqual(chunks[0].page_number, 1)

    def test_long_text_splits(self):
        text = "Paragraph one. " * 80
        chunks = split_pages([(2, text)], chunk_size=120, overlap=20)
        self.assertGreater(len(chunks), 1)
        self.assertTrue(all(c.page_number == 2 for c in chunks))

    def test_numbered_heading_becomes_section(self):
        pages = [
            (1, "2. Minimum Attendance\nStudents must maintain at least 75% attendance.")
        ]
        chunks = split_pages(pages, chunk_size=400, overlap=20)
        self.assertTrue(chunks)
        self.assertIn("Minimum Attendance", chunks[0].section)


class ExtractionTests(SimpleTestCase):
    def test_txt_extraction(self):
        with NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8") as handle:
            handle.write("Hello from Bahria policy.\n\nSecond paragraph.")
            path = handle.name
        pages = extract_pages(path, "txt")
        Path(path).unlink(missing_ok=True)
        self.assertEqual(pages[0][0], 1)
        self.assertIn("Bahria", pages[0][1])


class AnswerCleanupTests(SimpleTestCase):
    def test_reasoning_preamble_is_removed(self):
        raw = (
            "The user is asking for the exam policy. I need to scan the excerpts.\n"
            "Identify the core question.\n\n"
            "## General Examination Rules\n\n"
            "1. **Attendance:** Students with less than 75% attendance cannot appear in the Final Examination.\n"
            "Source: Handbook, Page 122\n"
        )
        cleaned = sanitize_answer(raw)
        self.assertTrue(cleaned.startswith("## General Examination Rules"))
        self.assertIn("**Attendance:**", cleaned)
        self.assertNotIn("The user is asking", cleaned)
        self.assertNotIn("Source:", cleaned)

    def test_thought_tags_are_removed(self):
        raw = (
            "<unused94>thought hidden analysis <unused95>"
            "## Mobile Phones\n\n1. **Devices:** Mobile phones are not allowed."
        )
        cleaned = sanitize_answer(raw)
        self.assertNotIn("hidden analysis", cleaned)
        self.assertIn("## Mobile Phones", cleaned)

    def test_greeting_gets_a_welcome_reply(self):
        result = answer_question("hello")
        self.assertTrue(result["found"])
        self.assertEqual(result["sources"], [])
        self.assertIn("Hello", result["answer"])
        self.assertNotEqual(result["answer"], NOT_FOUND_MESSAGE)

    def test_bot_identity_question_has_no_sources(self):
        result = answer_question("who are you?")
        self.assertTrue(result["found"])
        self.assertEqual(result["sources"], [])
        self.assertIn("Bahria University Policy Bot", result["answer"])
        self.assertIn("Why I exist", result["answer"])
        self.assertIn("What I do", result["answer"])

    def test_policy_question_starting_with_hi_is_not_treated_as_greeting(self):
        self.assertIsNone(_greeting_reply("hi, what is the attendance policy?"))

    def test_stream_greeting_is_immediate(self):
        events = list(stream_answer_events("hello"))
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["type"], "done")
        self.assertIn("Hello", events[0]["answer"])

    def test_partial_visible_hides_unfinished_thoughts(self):
        self.assertEqual(_partial_visible("<unused94>thought still going"), "")
        visible = _partial_visible(
            "<unused94>thought hidden <unused95>## Fees\n\n1. **Due:** Pay on time."
        )
        self.assertIn("## Fees", visible)
        self.assertNotIn("hidden", visible)
