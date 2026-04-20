import unittest

from cereal_killer.config import Settings
from cereal_killer.engine import LLMEngine, parse_llm_response
from mentor.engine.brain import Brain


class ParseLLMResponseTests(unittest.TestCase):
    def test_extracts_thought_tags(self) -> None:
        response = parse_llm_response("<thought>step 1</thought>final answer")
        self.assertEqual(response.thought, "step 1")
        self.assertEqual(response.answer, "final answer")

    def test_handles_multiple_and_case_insensitive_thought_tags(self) -> None:
        response = parse_llm_response("<THOUGHT>a</THOUGHT>ok<thought>b</thought>")
        self.assertEqual(response.thought, "a\n\nb")
        self.assertEqual(response.answer, "ok")

    def test_handles_no_thought_tag(self) -> None:
        response = parse_llm_response("plain answer")
        self.assertEqual(response.thought, "")
        self.assertEqual(response.answer, "plain answer")

    def test_extracts_response_from_plain_thought_template(self) -> None:
        payload = (
            "thought\n"
            "The user suspects command injection.\n"
            "Response:\n"
            "\"Try 127.0.0.1 first, then append ;whoami and report output.\""
        )
        response = parse_llm_response(payload)
        self.assertIn("suspects command injection", response.thought)
        self.assertEqual(
            response.answer,
            "Try 127.0.0.1 first, then append ;whoami and report output.",
        )

    def test_falls_back_to_thought_when_answer_empty(self) -> None:
        response = parse_llm_response("<thought>Use | as separator and inspect output</thought>")
        self.assertEqual(response.thought, "Use | as separator and inspect output")
        self.assertEqual(response.answer, "Use | as separator and inspect output")

    def test_extracts_response_section_from_mixed_text(self) -> None:
        payload = (
            "Some preamble from model.\n"
            "Response:\n"
            "\"Try 127.0.0.1|id and compare with baseline output.\""
        )
        response = parse_llm_response(payload)
        self.assertEqual(response.answer, "Try 127.0.0.1|id and compare with baseline output.")


class PruneThresholdTests(unittest.TestCase):
    def test_prune_threshold_is_80_percent_of_context(self) -> None:
        settings = Settings(max_model_len=262144)
        engine = LLMEngine(settings)
        # 262144 tokens * 3 chars/token * 0.80 = 629,145.6 → 629145
        expected = int(262144 * 3 * 0.80)
        self.assertEqual(engine.prune_threshold(), expected)

    def test_prune_target_is_60_percent_of_context(self) -> None:
        settings = Settings(max_model_len=262144)
        engine = LLMEngine(settings)
        expected = int(262144 * 3 * 0.60)
        self.assertEqual(engine.prune_target(), expected)

    def test_prune_threshold_larger_than_target(self) -> None:
        settings = Settings(max_model_len=262144)
        engine = LLMEngine(settings)
        self.assertGreater(engine.prune_threshold(), engine.prune_target())


class BrainContextHelpersTests(unittest.TestCase):
    def test_dedupe_messages_removes_consecutive_duplicates(self) -> None:
        messages = [
            {"role": "system", "content": "A"},
            {"role": "system", "content": "A"},
            {"role": "assistant", "content": "", "reasoning_content": "r"},
            {"role": "assistant", "content": "", "reasoning_content": "r"},
            {"role": "user", "content": "u"},
        ]
        deduped = Brain._dedupe_messages(messages)
        self.assertEqual(len(deduped), 3)
        self.assertEqual(deduped[0]["role"], "system")
        self.assertEqual(deduped[1]["role"], "assistant")
        self.assertEqual(deduped[2]["role"], "user")

    def test_similar_input_detects_variations(self) -> None:
        self.assertTrue(
            Brain._is_similar_input(
                "curl 'http://10.10.11.35/ip?ip=1.1.1.1;id'",
                "curl http://10.10.11.35/ip?ip=1.1.1.1; whoami",
            )
        )

    def test_stuck_status_prefers_ip_command_injection_message(self) -> None:
        status = Brain._build_stuck_status(
            [
                "Trying command injection on /ip parameter",
                "Still testing command injection on ip",
            ]
        )
        self.assertIn("/ip parameter", status)

    def test_normalise_completion_payload_promotes_reasoning_when_content_empty(self) -> None:
        content, reasoning = Brain._normalise_completion_payload("", "Reasoning-only output")
        self.assertEqual(content, "Reasoning-only output")
        self.assertEqual(reasoning, "")

    def test_normalise_completion_payload_preserves_regular_content(self) -> None:
        content, reasoning = Brain._normalise_completion_payload("Final answer", "Hidden thoughts")
        self.assertEqual(content, "Final answer")
        self.assertEqual(reasoning, "Hidden thoughts")


if __name__ == "__main__":
    unittest.main()
