import unittest

from cereal_killer.engine import parse_llm_response


class ParseLLMResponseTests(unittest.TestCase):
    def test_extracts_thought_tags(self) -> None:
        response = parse_llm_response("<thought>step 1</thought>final answer")
        self.assertEqual(response.thought, "step 1")
        self.assertEqual(response.answer, "final answer")


if __name__ == "__main__":
    unittest.main()
