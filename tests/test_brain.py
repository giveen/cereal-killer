import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from mentor.engine.brain import Brain


class BrainTests(unittest.TestCase):
    def test_extract_thoughts(self) -> None:
        text = "<thought>first</thought>answer<thought>second</thought>"
        answer, thoughts = Brain.extract_thoughts(text)
        self.assertEqual("answer", answer)
        self.assertEqual(["first", "second"], thoughts)

    def test_build_user_prompt_context_limit(self) -> None:
        brain = Brain.__new__(Brain)  # bypass external client init
        prompt = brain._build_user_prompt("hello", context_commands=[f"cmd{i}" for i in range(80)], cwd="/tmp/x")
        self.assertIn("Current working directory", prompt)
        self.assertIn("cmd79", prompt)
        self.assertNotIn("cmd0", prompt)


if __name__ == "__main__":
    unittest.main()
