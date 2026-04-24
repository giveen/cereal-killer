from __future__ import annotations

import asyncio
import unittest

from cereal_killer.ui.workers.worker_lifecycle import WorkerLifecycleManager


class _DummyApp:
    def __init__(self) -> None:
        self._active_workers: dict[str, asyncio.Task[None]] = {}
        self._analysis_jobs = 0
        self.screen = object()
        self.screen_stack = []


class WorkerLifecycleManagerTests(unittest.IsolatedAsyncioTestCase):
    async def test_with_worker_cancellation_skips_current_task(self) -> None:
        app = _DummyApp()
        manager = WorkerLifecycleManager(app)

        sleeper = asyncio.create_task(asyncio.sleep(60))
        current = asyncio.current_task()
        assert current is not None

        app._active_workers["sleeper"] = sleeper
        app._active_workers["current"] = current

        result = await manager.with_worker_cancellation(asyncio.sleep(0, result="ok"))

        self.assertEqual(result, "ok")
        self.assertTrue(sleeper.cancelled())
        self.assertFalse(current.cancelled())

    async def test_analysis_busy_handles_missing_dashboard(self) -> None:
        app = _DummyApp()
        manager = WorkerLifecycleManager(app)

        manager.analysis_busy(True)
        manager.analysis_busy(False)

        self.assertEqual(app._analysis_jobs, 0)


if __name__ == "__main__":
    unittest.main()
