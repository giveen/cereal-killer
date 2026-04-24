from __future__ import annotations

import asyncio
import unittest

from cereal_killer.ui.sessions.session_manager import SessionManager


class _DummyEngine:
    def __init__(self) -> None:
        self.persisted: list[list[str]] = []

    async def persist_mental_state(self, history_commands: list[str]) -> None:
        self.persisted.append(list(history_commands))

    def active_pathetic_meter(self) -> int:
        return 3


class _DummyApp:
    def __init__(self) -> None:
        self.engine = _DummyEngine()
        self.last_code_block = ""
        self.pathetic_meter = 0
        self.easy_usage_count = 0
        self.successful_command_count = 0
        self.current_target = ""
        self._context_history = ["active-a", "active-b"]
        self._context_chat = [{"role": "assistant", "text": "active"}]

    @property
    def active_history(self) -> list[str]:
        return list(self._context_history)

    @property
    def active_transcript(self) -> list[dict[str, str]]:
        return list(self._context_chat)


class SessionManagerTests(unittest.IsolatedAsyncioTestCase):
    async def test_schedule_persist_uses_active_history(self) -> None:
        app = _DummyApp()
        manager = SessionManager(app)

        manager.schedule_persist_mental_state()
        await asyncio.sleep(0)

        self.assertEqual(app.engine.persisted, [["active-a", "active-b"]])


if __name__ == "__main__":
    unittest.main()
