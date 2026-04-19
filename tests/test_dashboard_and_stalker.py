import tempfile
import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from mentor.observer.stalker import HistoryStalker
from mentor.ui.main_dashboard import MainDashboard
from mentor.engine.brain import BrainResponse


class FakeBrain:
    def __init__(self) -> None:
        self.calls = []
        self.on_thoughts = None

    async def process_command(self, command: str, context: list[str], cwd: str) -> None:
        self.calls.append((command, list(context), cwd))

    async def ask(self, prompt: str, context_commands=None, cwd=None):
        return BrainResponse(
            answer="Use this:\n```bash\necho test\n```",
            thoughts=["think"],
            raw_text="raw",
        )


class DashboardAndStalkerTests(unittest.IsolatedAsyncioTestCase):
    async def test_stalker_dispatches_technical_commands(self) -> None:
        brain = FakeBrain()
        with tempfile.NamedTemporaryFile(mode="w+", delete=False) as f:
            path = Path(f.name)
            f.write(": 1:0;nmap -sV target\n")
            f.flush()

        stalker = HistoryStalker(brain=brain, history_file=path)
        await stalker.run_once()
        self.assertEqual(1, len(brain.calls))
        self.assertEqual("nmap -sV target", brain.calls[0][0])

    async def test_dashboard_routes_prompt_and_copy(self) -> None:
        copied = []
        dashboard = MainDashboard(brain=FakeBrain(), copy_handler=copied.append)
        dashboard.add_terminal_command("nmap -sV target")
        reply = await dashboard.submit_prompt("What now?")
        self.assertTrue(reply.code_blocks)
        payload = dashboard.copy_code_block(0)
        self.assertEqual(payload, copied[0])


if __name__ == "__main__":
    unittest.main()
