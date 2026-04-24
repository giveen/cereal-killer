from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from cereal_killer.ui.app import CerealKillerApp

from ..base import resolve_dashboard


class WorkerLifecycleManager:
    """Manages worker task lifecycle for the CerealKiller app.

    Extracted from CerealKillerApp to encapsulate worker registration,
    cancellation, and analysis job tracking logic.
    """

    def __init__(self, app: "CerealKillerApp") -> None:
        self._app = app

    @property
    def active_workers(self) -> dict[str, Any]:
        """Return the dictionary of active worker tasks."""
        return self._app._active_workers

    @property
    def analysis_jobs(self) -> int:
        """Return the current analysis job count."""
        return self._app._analysis_jobs

    @analysis_jobs.setter
    def analysis_jobs(self, value: int) -> None:
        """Set the analysis job count."""
        self._app._analysis_jobs = value

    def register_worker(self, name: str, task: Any) -> None:
        """Register a worker task for tracking/cancellation.

        Args:
            name: Unique identifier for the worker.
            task: The asyncio.Task to track.
        """
        self._app._active_workers[name] = task

    def cancel_worker(self, name: str) -> None:
        """Cancel a specific worker by name.

        Args:
            name: The worker identifier to cancel.
        """
        if name in self._app._active_workers:
            task = self._app._active_workers[name]
            if not task.done():
                task.cancel()
            self._app._active_workers.pop(name, None)

    def cancel_all_workers(self) -> None:
        """Cancel all pending workers to prevent race conditions."""
        for name, task in list(self._app._active_workers.items()):
            if not task.done():
                task.cancel()
        self._app._active_workers.clear()

    def unregister_worker(self, name: str) -> None:
        """Unregister a completed worker.

        Args:
            name: The worker identifier to unregister.
        """
        self._app._active_workers.pop(name, None)

    async def with_worker_cancellation(self, coro: Any) -> Any:
        """Cancel all existing workers before running the given coroutine.

        Prevents race conditions when multiple @work workers share
        the active per-box context.
        Args:
            coro: The coroutine to await after cancelling existing workers.

        Returns:
            The result of the awaited coroutine.

        Raises:
            asyncio.CancelledError: If the coroutine is cancelled.
        """
        current_task = asyncio.current_task()
        for name, task in list(self._app._active_workers.items()):
            if task is current_task:
                continue
            if not task.done():
                task.cancel()
        try:
            return await coro
        except asyncio.CancelledError:
            raise

    def analysis_busy(self, active: bool) -> None:
        """Mark analysis as busy/idle and update dashboard loading state.

        Args:
            active: True to increment the job counter, False to decrement.
        """
        if active:
            self._app._analysis_jobs += 1
        else:
            self._app._analysis_jobs = max(0, self._app._analysis_jobs - 1)
        dashboard = resolve_dashboard(self._app)
        if dashboard is not None:
            dashboard.set_loading(self._app._analysis_jobs > 0)

    _dashboard = resolve_dashboard
