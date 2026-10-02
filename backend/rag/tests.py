from unittest.mock import patch
import json

from rag.chunking import split_pages
from rag.extraction import extract_pages
from rag.prompts import NOT_FOUND_MESSAGE
from rag.qa import (
    _continue_from_short_reply,
    _ensure_follow_up,
    _finalize_answer,
    _greeting_reply,
    _partial_visible,
    _split_follow_up,
    answer_question,
    sanitize_answer,
    stream_answer_events,
)
from rag.groq_client import stream_generate
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
        self.assertNotIn("Suggested question", result["answer"])
        _main, follow = _split_follow_up(result["answer"])
        self.assertTrue(follow.endswith("?"))

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
        self.assertNotIn("Suggested question", events[-1]["answer"])
        self.assertIn("?", events[-1]["answer"])

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
        self.assertTrue(done["answer"].startswith("Hello! Kaise madad kar sakta hoon?"))
        self.assertNotIn("Suggested question", done["answer"])
        self.assertNotIn("</think>", done["answer"])
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


class GroqStreamParseTests(SimpleTestCase):
    def test_stream_reads_delta_and_skips_reasoning_only_chunks(self):
        lines = [
            'data: {"choices":[{"delta":{"reasoning":"planning the reply"}}]}',
            'data: {"choices":[{"delta":{"content":"Hello"}}]}',
            'data: {"choices":[{"delta":{"content":"!"},"finish_reason":"stop"}]}',
            "data: [DONE]",
        ]

        class FakeResponse:
            ok = True

            def raise_for_status(self):
                return None

            def iter_lines(self, decode_unicode=True):
                return iter(lines)

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

        with patch("rag.groq_client.requests.post", return_value=FakeResponse()):
            with patch("rag.groq_client._headers", return_value={"Authorization": "Bearer test"}):
                chunks = list(stream_generate("sys", "user"))
        self.assertEqual(chunks, ["Hello", "!"])

    def test_qwen_payload_hides_reasoning(self):
        from rag.groq_client import _chat_payload

        payload = _chat_payload("sys", "user", stream=True)
        self.assertEqual(payload["reasoning_effort"], "none")
        self.assertEqual(payload["reasoning_format"], "hidden")
        self.assertIn("max_completion_tokens", payload)

    def test_otpm_error_retries_with_smaller_limit(self):
        from rag.groq_client import generate_answer

        class TooBig:
            ok = False
            status_code = 413

            def json(self):
                return {
                    "error": {
                        "message": (
                            "Request too large ... on output tokens per minute (OTPM): "
                            "Limit 1000, Requested 1024."
                        )
                    }
                }

            def close(self):
                return None

        class Ok:
            ok = True

            def json(self):
                return {"choices": [{"message": {"content": "OK"}}]}

        posts = [TooBig(), Ok()]

        def fake_post(*_args, **_kwargs):
            return posts.pop(0)

        with patch("rag.groq_client.requests.post", side_effect=fake_post):
            with patch("rag.groq_client._headers", return_value={"Authorization": "Bearer test"}):
                self.assertEqual(generate_answer("sys", "user"), "OK")


class FollowUpQuestionTests(SimpleTestCase):
    def test_keeps_model_follow_up_and_drops_extras(self):
        raw = (
            "Keep 75 percent attendance to sit the final exam.\n\n"
            "**Suggested question:** What happens if attendance falls below 75%?\n"
            "**Suggested question:** Any other question?"
        )
        cleaned = _ensure_follow_up("What is the attendance policy?", raw)
        self.assertTrue(cleaned.startswith("Keep 75 percent attendance to sit the final exam."))
        self.assertNotIn("Suggested question", cleaned)
        _main, follow = _split_follow_up(cleaned)
        self.assertEqual(follow, "What happens if attendance falls below 75%?")
        self.assertNotIn("Any other question?", cleaned)

    def test_follow_up_changes_with_the_query(self):
        attendance = _ensure_follow_up(
            "What is the attendance policy?",
            "Students must keep 75% attendance to sit the final exam.",
        )
        scholarship = _ensure_follow_up(
            "What GPA is required for a scholarship?",
            "Merit awards need a CGPA of 3.50.",
        )
        self.assertNotIn("Suggested question", attendance)
        self.assertNotIn("Suggested question", scholarship)
        _main_a, att_q = _split_follow_up(attendance)
        _main_s, sch_q = _split_follow_up(scholarship)
        self.assertNotEqual(att_q, sch_q)
        self.assertTrue(att_q.endswith("?"))
        self.assertTrue(sch_q.endswith("?"))

    def test_yes_continues_the_previous_follow_up(self):
        history = [
            {"role": "user", "content": "What is the attendance policy?"},
            {
                "role": "assistant",
                "content": (
                    "Students must keep 75 percent attendance.\n\n"
                    "Would you like to know what happens if attendance falls below 75%?"
                ),
            },
        ]
        excerpt = (
            "Students with less than 75 percent attendance in a registered course "
            "cannot sit the final examination under the official handbook rule."
        )
        with patch(
            "rag.qa.retrieve_policy_chunks",
            return_value=(
                [
                    {
                        "content": excerpt,
                        "metadata": {
                            "document_title": "Attendance Policy",
                            "document_id": 1,
                            "page_number": 1,
                            "chunk_index": 0,
                        },
                        "relevance_score": 0.9,
                        "vector_id": "1",
                    }
                ],
                "vector",
            ),
        ):
            with patch(
                "rag.qa.generate_answer",
                return_value="If attendance falls short you cannot sit the final exam.",
            ) as mocked:
                result = answer_question("yes", history)
        self.assertTrue(mocked.called)
        self.assertIn("falls below 75%", mocked.call_args[0][1])
        self.assertIn("cannot sit", result["answer"].lower())
        self.assertNotIn("Suggested question", result["answer"])

    def test_no_keeps_the_conversation_open(self):
        history = [
            {"role": "user", "content": "What is the attendance policy?"},
            {
                "role": "assistant",
                "content": "Keep 75 percent attendance.\n\nWould you like the next step related to this attendance policy?",
            },
        ]
        with patch("rag.qa.retrieve_policy_chunks") as mocked:
            result = answer_question("no", history)
        mocked.assert_not_called()
        self.assertNotIn("Suggested question", result["answer"])
        self.assertNotIn(NOT_FOUND_MESSAGE, result["answer"])
        self.assertIn("handbook", result["answer"].lower())
        self.assertNotIn("repeat", result["answer"].lower())

    def test_short_replies_resolve_the_last_suggestion(self):
        history = [
            {"role": "user", "content": "What is the attendance policy?"},
            {
                "role": "assistant",
                "content": (
                    "Keep 75 percent attendance to sit the exam.\n\n"
                    "Would you like me to explain this with an example?"
                ),
            },
        ]
        for reply in ("Yes", "Sure", "Okay", "Please do", "Tell me more"):
            resolved, declined = _continue_from_short_reply(reply, history)
            self.assertIsNone(declined, reply)
            self.assertIn("example", resolved.lower(), reply)
            self.assertIn("attendance", resolved.lower(), reply)
            self.assertNotEqual(resolved.strip().lower(), reply.strip().lower(), reply)
        resolved, declined = _continue_from_short_reply("No", history)
        self.assertEqual(resolved, "No")
        self.assertIsNotNone(declined)
        self.assertIn("skip", declined.lower())
        self.assertNotIn("repeat", declined.lower())


class RetrievalGuardTests(SimpleTestCase):
    def test_keeps_hits_when_topic_words_do_not_match(self):
        from rag.retriever import _prefer_on_topic

        hits = [
            {
                "content": "Merit awards need a CGPA of 3.50 for continuation of support.",
                "metadata": {"document_title": "Awards"},
                "relevance_score": 0.41,
            }
        ]
        kept = _prefer_on_topic(hits, "What is the scholarship policy?")
        self.assertEqual(kept, hits)

    def test_standalone_questions_do_not_mix_old_user_turns(self):
        from rag.retriever import _retrieval_query

        history = [{"role": "user", "content": "What is the attendance policy?"}]
        self.assertEqual(
            _retrieval_query("What is the examination policy?", history),
            "What is the examination policy?",
        )
        self.assertIn("attendance", _retrieval_query("tell me more", history).lower())

    def test_admission_cgpa_question_also_searches_scholarships(self):
        from rag.retriever import _confident, _ranked, _retrieval_query
        from rag.topics import expanded_topics, query_expansion

        question = (
            "i have 2.7 cgpa and i want to get admission in bahria university. "
            "what should i do and what are the benefits bahria university is giving for this cgpa"
        )
        self.assertIn("scholarship", expanded_topics(question))
        self.assertIn("scholarship", query_expansion(question).lower())
        self.assertIn("scholarship", _retrieval_query(question, []).lower())

        admission_hits = [
            {
                "content": (
                    "Undergraduate admissions require an entry test, interview, "
                    "and submission of the intermediate result card at the campus office."
                ),
                "metadata": {"document_title": "Admissions"},
                "relevance_score": 0.82,
            },
            {
                "content": (
                    "Applicants must complete the online admission form and pay "
                    "the processing fee before the published deadline for the semester."
                ),
                "metadata": {"document_title": "Admissions"},
                "relevance_score": 0.80,
            },
        ]
        self.assertFalse(_confident(admission_hits, question))

        mixed = admission_hits + [
            {
                "content": (
                    "Need based scholarships remain active when the student keeps "
                    "a minimum CGPA of 2.50 in each semester of the degree program."
                ),
                "metadata": {"document_title": "Scholarships"},
                "relevance_score": 0.40,
            }
        ]
        ranked = _ranked(mixed, question)
        self.assertIn("2.50", ranked[0]["content"])

    def test_direct_scholarship_cgpa_question_stays_confident(self):
        from rag.retriever import _confident, _retrieval_query

        question = "i have 2.9 cgpa. am i eligible for scholarship?"
        self.assertEqual(_retrieval_query(question, []), question)
        hits = [
            {
                "content": (
                    "Students may apply for a scholarship when their CGPA is at least "
                    "2.50 and they meet the other published financial aid conditions."
                ),
                "metadata": {"document_title": "Scholarships"},
                "relevance_score": 0.88,
            },
            {
                "content": (
                    "The scholarship office reviews applications each semester and "
                    "notifies students after the results are declared by the exam branch."
                ),
                "metadata": {"document_title": "Scholarships"},
                "relevance_score": 0.70,
            },
        ]
        self.assertTrue(_confident(hits, question))

    def test_model_not_found_still_uses_retrieved_excerpts(self):
        excerpt = (
            "Students must keep seventy five percent attendance to sit in the "
            "final examination under the official handbook rule."
        )
        text = _finalize_answer(
            NOT_FOUND_MESSAGE,
            {
                "hits": [{"content": excerpt, "metadata": {}}],
                "retrieval": "vector",
            },
        )
        self.assertNotEqual(text, NOT_FOUND_MESSAGE)
        self.assertIn("seventy five", text.lower())


class EncodingCleanupTests(SimpleTestCase):
    def test_repairs_mojibake_university_apostrophe(self):
        from rag.extraction import normalize_policy_text

        broken = "Bahria University" + bytes((0xE2, 0x80, 0x99)).decode("cp1252") + "s handbook"
        self.assertIn("â", broken)
        self.assertEqual(normalize_policy_text(broken), "Bahria University's handbook")
        cleaned = sanitize_answer(broken + " applies to every student.")
        self.assertIn("University's", cleaned)
        self.assertNotIn("â", cleaned)

    def test_normalizes_curly_apostrophe_and_html_entity(self):
        from rag.extraction import normalize_policy_text

        self.assertEqual(normalize_policy_text("University\u2019s"), "University's")
        self.assertEqual(normalize_policy_text("University&rsquo;s"), "University's")
        leftover = "Bahria Universityâ\u2019s attendance rule is 75 percent."
        self.assertNotIn("â", sanitize_answer(leftover))


class EmbeddingOfflineTests(SimpleTestCase):
    def setUp(self):
        from rag.embeddings import get_embedding_service

        get_embedding_service.cache_clear()
        self.addCleanup(get_embedding_service.cache_clear)

    def test_finds_local_minilm_folder_without_huggingface(self):
        from tempfile import TemporaryDirectory

        from django.test import override_settings

        from rag.embeddings import find_local_embedding_model

        with TemporaryDirectory() as raw:
            model_dir = Path(raw) / "all-MiniLM-L6-v2"
            model_dir.mkdir()
            (model_dir / "modules.json").write_text("[]", encoding="utf-8")
            with override_settings(EMBEDDING_MODEL_PATH=str(model_dir), EMBEDDING_MODEL="all-MiniLM-L6-v2"):
                found = find_local_embedding_model("all-MiniLM-L6-v2")
        self.assertEqual(found, model_dir)

    def test_sentence_transformers_falls_back_to_lexical_when_hf_is_down(self):
        from unittest.mock import patch

        from django.test import override_settings

        from rag.embeddings import LEXICAL_DIM, get_embedding_service

        hf_error = OSError(
            "We couldn't connect to huggingface.co to load the files, "
            "and couldn't find them in the cached files."
        )
        with override_settings(
            EMBEDDING_PROVIDER="sentence-transformers",
            EMBEDDING_MODEL="all-MiniLM-L6-v2",
            EMBEDDING_MODEL_PATH="",
        ):
            with patch("rag.embeddings.find_local_embedding_model", return_value=None):
                with patch("rag.embeddings._load_sentence_transformer", side_effect=hf_error):
                    service = get_embedding_service()
                    vectors = service.embed_texts(
                        ["Students must keep seventy five percent attendance."]
                    )
        self.assertEqual(len(vectors), 1)
        self.assertEqual(len(vectors[0]), LEXICAL_DIM)
        self.assertEqual(service.active_provider, "lexical")
        self.assertIn("lexical", service.active_source)

    def test_local_minilm_is_loaded_with_local_files_only(self):
        from tempfile import TemporaryDirectory
        from unittest.mock import MagicMock, patch

        from django.test import override_settings

        from rag.embeddings import LEXICAL_DIM, get_embedding_service

        fake_model = MagicMock()
        fake_model.encode.return_value = [[0.05] * LEXICAL_DIM]
        with TemporaryDirectory() as raw:
            model_dir = Path(raw) / "minilm"
            model_dir.mkdir()
            (model_dir / "modules.json").write_text("[]", encoding="utf-8")
            with override_settings(
                EMBEDDING_PROVIDER="sentence-transformers",
                EMBEDDING_MODEL="all-MiniLM-L6-v2",
                EMBEDDING_MODEL_PATH=str(model_dir),
            ):
                with patch("rag.embeddings._load_sentence_transformer", return_value=fake_model) as loader:
                    service = get_embedding_service()
                    vectors = service.embed_texts(["Attendance policy"])
        loader.assert_called_once_with(str(model_dir), local_files_only=True)
        self.assertEqual(service.active_provider, "sentence-transformers")
        self.assertTrue(str(model_dir) in service.active_source)
        self.assertEqual(len(vectors[0]), LEXICAL_DIM)
