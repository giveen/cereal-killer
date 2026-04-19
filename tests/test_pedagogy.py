"""Tests for mentor.engine.pedagogy – Socratic state machine."""
from __future__ import annotations

import time
import unittest
from unittest.mock import patch

from mentor.engine.pedagogy import (
    HintLevel,
    PedagogyEngine,
    _VAGUE_SECONDS,
    _CONCEPTUAL_SECONDS,
)


def _freeze_engine(age_seconds: float) -> PedagogyEngine:
    """Return a PedagogyEngine whose stuck-timer reads *age_seconds* old."""
    engine = PedagogyEngine()
    engine._last_progress_at = time.monotonic() - age_seconds
    return engine


class TestHintLevelTransitions(unittest.TestCase):
    def test_fresh_engine_is_vague(self):
        eng = _freeze_engine(0)
        self.assertEqual(eng.current_hint_level(), HintLevel.VAGUE)

    def test_just_under_vague_threshold_is_vague(self):
        eng = _freeze_engine(_VAGUE_SECONDS - 1)
        self.assertEqual(eng.current_hint_level(), HintLevel.VAGUE)

    def test_just_over_vague_threshold_is_conceptual(self):
        eng = _freeze_engine(_VAGUE_SECONDS + 1)
        self.assertEqual(eng.current_hint_level(), HintLevel.CONCEPTUAL)

    def test_just_under_conceptual_threshold_is_conceptual(self):
        eng = _freeze_engine(_CONCEPTUAL_SECONDS - 1)
        self.assertEqual(eng.current_hint_level(), HintLevel.CONCEPTUAL)

    def test_just_over_conceptual_threshold_is_direct(self):
        eng = _freeze_engine(_CONCEPTUAL_SECONDS + 1)
        self.assertEqual(eng.current_hint_level(), HintLevel.DIRECT)


class TestRecordCommandResetsTimer(unittest.TestCase):
    def test_record_command_resets_to_vague(self):
        eng = _freeze_engine(_CONCEPTUAL_SECONDS + 5)
        self.assertEqual(eng.current_hint_level(), HintLevel.DIRECT)
        eng.record_command()
        self.assertEqual(eng.current_hint_level(), HintLevel.VAGUE)


class TestRecordPhaseChange(unittest.TestCase):
    def test_new_phase_resets_timer(self):
        eng = _freeze_engine(_CONCEPTUAL_SECONDS + 5)
        self.assertEqual(eng.current_hint_level(), HintLevel.DIRECT)
        eng.record_phase_change("[EXPLOITATION]")
        self.assertEqual(eng.current_hint_level(), HintLevel.VAGUE)

    def test_same_phase_does_not_reset_timer(self):
        eng = _freeze_engine(_CONCEPTUAL_SECONDS + 5)
        eng._current_phase = "[EXPLOITATION]"
        eng.record_phase_change("[EXPLOITATION]")
        self.assertEqual(eng.current_hint_level(), HintLevel.DIRECT)


class TestShouldAllowWebSearch(unittest.TestCase):
    def test_vague_blocks_web(self):
        eng = _freeze_engine(0)
        self.assertFalse(eng.should_allow_web_search())

    def test_conceptual_blocks_web(self):
        eng = _freeze_engine(_VAGUE_SECONDS + 1)
        self.assertFalse(eng.should_allow_web_search())

    def test_direct_allows_web(self):
        eng = _freeze_engine(_CONCEPTUAL_SECONDS + 1)
        self.assertTrue(eng.should_allow_web_search())


class TestSystemPromptAddendum(unittest.TestCase):
    def test_addendum_contains_vague_text(self):
        eng = _freeze_engine(0)
        text = eng.system_prompt_addendum()
        self.assertIn("CRYPTIC", text)

    def test_addendum_contains_direct_text(self):
        eng = _freeze_engine(_CONCEPTUAL_SECONDS + 1)
        text = eng.system_prompt_addendum()
        self.assertIn("DIRECT", text)
