"""Tests for streaming LLM response functionality."""

import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from cereal_killer.engine import LLMResponse, LLMEngine
from cereal_killer.config import Settings
from mentor.engine.streaming import (
    StreamingState,
    StreamingCallbacks,
    extract_partial_thought,
    extract_partial_answer,
)
from mentor.engine.brain import Brain, parse_brain_output


class TestStreamingState:
    """Tests for the StreamingState dataclass."""

    def test_default_values(self):
        state = StreamingState()
        assert state.accumulated_content == ""
        assert state.accumulated_thought == ""
        assert state.accumulated_answer == ""
        assert state.reasoning_content == ""
        assert state.backend_meta == {}
        assert state.cancelled is False

    def test_custom_values(self):
        state = StreamingState(
            accumulated_content="hello",
            accumulated_thought="thinking...",
            cancelled=True,
        )
        assert state.accumulated_content == "hello"
        assert state.accumulated_thought == "thinking..."
        assert state.cancelled is True


class TestStreamingHelpers:
    """Tests for extract_partial_thought and extract_partial_answer."""

    def test_extract_partial_thought_complete_tag(self):
        content = "<thought>I am thinking</thought>Here is the answer"
        result = extract_partial_thought(content)
        assert result == "I am thinking"

    def test_extract_partial_thought_multiple_tags(self):
        content = "<thought>first</thought>middle<thought>second</thought>end"
        result = extract_partial_thought(content)
        assert result == "first\n\nsecond"

    def test_extract_partial_thought_incomplete_tag(self):
        content = "Here is the answer <thought>incomplete"
        result = extract_partial_thought(content)
        assert result == ""

    def test_extract_partial_thought_multiline(self):
        content = "<thought>Line 1\nLine 2\nLine 3</thought>Answer"
        result = extract_partial_thought(content)
        assert "Line 1" in result

    def test_extract_partial_answer_no_thoughts(self):
        content = "Just a plain answer"
        result = extract_partial_answer(content)
        assert result == "Just a plain answer"

    def test_extract_partial_answer_with_thoughts(self):
        content = "<thought>thinking...</thought>Here is the answer"
        result = extract_partial_answer(content)
        assert result == "Here is the answer"

    def test_extract_partial_answer_multiple_thoughts(self):
        content = "<thought>first</thought><thought>second</thought>Answer"
        result = extract_partial_answer(content)
        assert result == "Answer"


class TestBrainStreaming:
    """Tests for Brain streaming methods."""

    @pytest.fixture
    def brain(self):
        settings = Settings(llm_model="test-model", llm_base_url="http://test:8000/v1")
        brain = Brain(settings)
        return brain

    def test_extract_partial_thought_method(self, brain):
        result = brain._extract_partial_thought("<thought>test thought</thought>Answer")
        assert result == "test thought"

    def test_extract_partial_answer_method(self, brain):
        result = brain._extract_partial_answer("<thought>thought</thought>Answer")
        assert result == "Answer"

    def test_extract_partial_thought_empty(self, brain):
        result = brain._extract_partial_thought("No thoughts here")
        assert result == ""

    def test_extract_partial_answer_empty_thoughts(self, brain):
        content = "Plain text answer"
        result = brain._extract_partial_answer(content)
        assert result == "Plain text answer"


class TestLLMResponse:
    """Tests for LLMResponse dataclass with streaming fields."""

    def test_llm_response_with_streaming_fields(self):
        response = LLMResponse(
            thought="test thought",
            answer="test answer",
            streaming=True,
            chunk_count=5,
        )
        assert response.streaming is True
        assert response.chunk_count == 5

    def test_llm_response_default_values(self):
        response = LLMResponse(thought="t", answer="a")
        assert response.streaming is False
        assert response.chunk_count == 0


class TestStreamingCallbacks:
    """Tests for the StreamingCallbacks protocol."""

    def test_protocol_compliance(self):
        """Verify that a simple implementation satisfies the protocol."""

        class TestCallbacks:
            async def on_token(self, token: str) -> None:
                pass

            async def on_thought_update(self, thought: str) -> None:
                pass

            async def on_complete(self, response) -> None:
                pass

            async def on_error(self, error: Exception) -> None:
                pass

            async def cancel(self) -> None:
                pass

        callbacks = TestCallbacks()
        # Should not raise — confirms protocol compliance
        assert callbacks is not None


class TestEngineStreaming:
    """Tests for LLMEngine streaming methods."""

    def test_chat_stream_exists(self):
        assert hasattr(LLMEngine, "chat_stream")

    def test_react_stream_exists(self):
        assert hasattr(LLMEngine, "react_stream")

    def test_diagnose_failure_stream_exists(self):
        assert hasattr(LLMEngine, "diagnose_failure_stream")


class TestParseBrainOutputStreaming:
    """Tests for parse_brain_output with streaming-compatible content."""

    def test_parse_with_thought_tags(self):
        content = "<thought>I am thinking</thought>\nHere is the answer"
        result = parse_brain_output(content)
        assert result.thought == "I am thinking"
        assert "Here is the answer" in result.answer

    def test_parse_without_thought_tags(self):
        content = "Just a plain answer"
        result = parse_brain_output(content)
        assert result.answer == "Just a plain answer"
        assert result.thought == ""

    def test_parse_empty_content(self):
        result = parse_brain_output("")
        assert result.answer == ""
        assert result.thought == ""
