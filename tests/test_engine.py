import unittest

from cereal_killer.engine import parse_llm_response


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


if __name__ == "__main__":
    unittest.main()
