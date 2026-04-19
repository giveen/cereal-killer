import unittest

from cereal_killer.config import Settings
from cereal_killer.engine import LLMEngine, parse_llm_response


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


if __name__ == "__main__":
    unittest.main()
