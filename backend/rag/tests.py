from unittest.mock import patch
import json

from rag.chunking import split_pages
from rag.extraction import extract_pages
from rag.prompts import NOT_FOUND_MESSAGE
from rag.qa import _finalize_answer, _greeting_reply, _partial_visible, answer_question, sanitize_answer, stream_answer_events
from rag.ollama_client import stream_generate
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
        with patch(
            "rag.qa.generate_answer",
            return_value="Wa alaikum assalam. How can I help with a university policy?",
        ):
            result = answer_question("salam")
        self.assertTrue(result["found"])
        self.assertEqual(result["sources"], [])
        self.assertIn("alaikum", result["answer"].lower())
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
        with patch(
            "rag.qa.stream_generate",
            return_value=iter(["Wa alaikum assalam. ", "How can I help?"]),
        ):
            events = list(stream_answer_events("assalam o alaikum"))
        self.assertGreaterEqual(len(events), 2)
        self.assertEqual(events[-1]["type"], "done")
        self.assertIn("alaikum", events[-1]["answer"].lower())

    def test_qwen_thinking_is_hidden(self):
        raw = (
            "Okay, let me tackle this user query about Bahria University's attendance policy. "
            "The user specifically asked for a 2-line definition.\n\n"
            "Looking at the provided policy excerpts (all from page 32 of the Student Handbook), I see key points.\n\n"
            "*Double-checking*: 75% is the exact figure."
        )
        self.assertEqual(_partial_visible(raw), "")
        cleaned = sanitize_answer(raw)
        self.assertNotIn("let me tackle", cleaned.lower())
        self.assertNotIn("Double-checking", cleaned)

    def test_qwen_thinking_then_answer(self):
        raw = (
            "Okay, let me tackle this user query about attendance.\n\n"
            "Students must maintain 75% attendance in each course or they cannot sit the final examination. "
            "Attendance shortfalls are not condoned."
        )
        cleaned = sanitize_answer(raw)
        self.assertIn("75%", cleaned)
        self.assertNotIn("let me tackle", cleaned.lower())
        self.assertEqual(_partial_visible(raw), "")

    def test_think_close_without_open_tag_is_hidden(self):
        raw = (
            "I'll say something like 'Hello! How can I help you today?' but in Roman Urdu style.\n\n"
            "Hmm... 'Hello' in Roman Urdu could be 'Hello!' or 'Salam!'\n\n"
            "The safest approach is to use 'Hello!' as the greeting.\n\n"
            "Perfect. I'll respond with just that - no extra words.\n"
            "</think>\n"
            "Hello! Kaise madad kar sakta hoon?"
        )
        self.assertEqual(_partial_visible(raw.split("</think>")[0]), "")
        cleaned = sanitize_answer(raw)
        self.assertEqual(cleaned, "Hello! Kaise madad kar sakta hoon?")
        self.assertNotIn("safest approach", cleaned)
        self.assertNotIn("</think>", cleaned)
        self.assertEqual(_partial_visible(raw), "Hello! Kaise madad kar sakta hoon?")

    def test_stream_emits_only_final_answer_after_think(self):
        chunks = [
            "I'll say something like Hello in Roman Urdu style. ",
            "The safest approach is Salam.\n</think>\n",
            "Hello! Kaise madad kar sakta hoon?",
        ]
        with patch("rag.qa.stream_generate", return_value=iter(chunks)):
            events = list(stream_answer_events("roman urdu me baat karo muj sy"))
        deltas = [item["text"] for item in events if item["type"] == "delta"]
        done = [item for item in events if item["type"] == "done"][-1]
        self.assertTrue(deltas)
        self.assertEqual(deltas[-1], "Hello! Kaise madad kar sakta hoon?")
        self.assertEqual(done["answer"], "Hello! Kaise madad kar sakta hoon?")
        self.assertNotIn("</think>", done["answer"])
        self.assertEqual(done["sources"], [])

    def test_instruction_echo_is_hidden(self):
        raw = (
            'We are given a user message: "/no_think\\nhi" As the Bahria University Policy Bot, we must:\n\n'
            "If the user greets (including salam or dua), reply in kind in two short, warm sentences.\n\n"
            "Let's craft two short, warm sentences:\n\n"
            'Example: "Assalamu Alaikum! How can I assist you today?" But note: the user didn\'t'
        )
        self.assertEqual(_partial_visible(raw), "")
        cleaned = sanitize_answer(raw)
        self.assertNotIn("/no_think", cleaned)
        self.assertNotIn("We are given a user message", cleaned)

    def test_partial_visible_hides_unfinished_thoughts(self):
        self.assertEqual(_partial_visible("<unused94>thought still going"), "")
        visible = _partial_visible(
            "<unused94>thought hidden <unused95>## Fees\n\n1. **Due:** Pay on time."
        )
        self.assertIn("## Fees", visible)
        self.assertNotIn("hidden", visible)

    def test_finalize_keeps_streamed_wording(self):
        prepared = {
            "hits": [
                {
                    "content": "Students must maintain 75 percent attendance in each registered course to sit the final examination."
                }
            ],
            "retrieval": "vector",
        }
        answer = _finalize_answer(
            NOT_FOUND_MESSAGE,
            prepared,
            visible="Keep 75 percent attendance if you want to sit the final exam.",
        )
        self.assertEqual(
            answer,
            "Keep 75 percent attendance if you want to sit the final exam.",
        )
        self.assertNotIn("Here is the helpful point", answer)


class OllamaStreamParseTests(SimpleTestCase):
    def test_stream_reads_message_and_skips_thinking_only_chunks(self):
        lines = [
            json.dumps({"message": {"thinking": "planning the reply", "content": ""}}),
            json.dumps({"message": {"content": "Hello"}}),
            json.dumps({"message": {"content": "!"}, "done": True}),
        ]

        class FakeResponse:
            def raise_for_status(self):
                return None

            def iter_lines(self, decode_unicode=True):
                return iter(lines)

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

        with patch("rag.ollama_client.requests.post", return_value=FakeResponse()):
            chunks = list(stream_generate("sys", "user"))
        self.assertEqual(chunks, ["Hello", "!"])
