import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from mentor.utils.minify import minify_tool_output


class MinifyTests(unittest.TestCase):
    def test_nmap_header_removed(self) -> None:
        raw = "Starting Nmap 7.95\nNmap scan report for host\n22/tcp open ssh\nNmap done: 1 IP address"
        result = minify_tool_output("nmap", raw)
        self.assertEqual("22/tcp open ssh", result)

    def test_truncates_long_content(self) -> None:
        result = minify_tool_output("other", "x" * 5000, max_chars=100)
        self.assertTrue(result.endswith("...[truncated]"))


if __name__ == "__main__":
    unittest.main()

