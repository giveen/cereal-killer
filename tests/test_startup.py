"""Tests for the Gibson boot sequence module."""
from __future__ import annotations

import asyncio
import unittest

from mentor.ui.startup import CheckResult, run_boot_sequence


def _collect(coro_gen) -> list[CheckResult]:
    """Drain the async generator into a list synchronously."""
    results: list[CheckResult] = []

    async def _gather() -> None:
        async for r in coro_gen:
            results.append(r)

    asyncio.run(_gather())
    return results


class BootSequenceTests(unittest.TestCase):
    def test_returns_check_results(self) -> None:
        from cereal_killer.config import Settings

        # Use a Redis URL that will always fail (unit test, no Redis running).
        settings = Settings(redis_url="redis://localhost:59999", llm_base_url="http://localhost:59998/v1")
        results = _collect(run_boot_sequence(settings))
        self.assertGreater(len(results), 0)
        for r in results:
            self.assertIsInstance(r, CheckResult)
            self.assertIsInstance(r.label, str)
            self.assertIsInstance(r.ok, bool)
            self.assertIsInstance(r.message, str)

    def test_always_starts_with_banner_and_ends_with_complete(self) -> None:
        from cereal_killer.config import Settings

        settings = Settings(redis_url="redis://localhost:59999", llm_base_url="http://localhost:59998/v1")
        results = _collect(run_boot_sequence(settings))
        # First entry is the boot banner.
        self.assertEqual(results[0].label, "BOOT")
        self.assertIn("GIBSON", results[0].message)
        # Last entry is the completion banner.
        self.assertEqual(results[-1].label, "BOOT")
        self.assertIn("COMPLETE", results[-1].message)

    def test_redis_failure_is_not_ok(self) -> None:
        from cereal_killer.config import Settings

        settings = Settings(redis_url="redis://localhost:59999")
        results = _collect(run_boot_sequence(settings))
        redis_result = next((r for r in results if r.label == "Redis"), None)
        self.assertIsNotNone(redis_result)
        assert redis_result is not None
        self.assertFalse(redis_result.ok)
        self.assertIn("FAIL", redis_result.message)

    def test_ippsec_failure_contains_warn(self) -> None:
        from cereal_killer.config import Settings

        settings = Settings(redis_url="redis://localhost:59999")
        results = _collect(run_boot_sequence(settings))
        ippsec_result = next((r for r in results if r.label == "IppSec"), None)
        self.assertIsNotNone(ippsec_result)
        assert ippsec_result is not None
        self.assertFalse(ippsec_result.ok)
        # IppSec failures are warnings, not hard errors.
        self.assertTrue(
            "WARN" in ippsec_result.message or "FAIL" in ippsec_result.message,
            ippsec_result.message,
        )


if __name__ == "__main__":
    unittest.main()
