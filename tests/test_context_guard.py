"""Tests for src/context_guard.py — truncation, web fallback detection."""
from __future__ import annotations

import pytest

from src.context_guard import (
    build_web_fallback_prompt,
    needs_web_fallback,
    truncate_prompt,
)


def test_truncate_short_prompt_unchanged():
    prompt = "Hello world"
    result = truncate_prompt(prompt, model_alias="claude-3-5-sonnet", reserve_chars=0)
    assert result == prompt


def test_truncate_long_prompt_middle_removed():
    # Modern llama models support 128k tokens ≈ 448k chars — use 600k to force truncation
    big = "A" * 300_000 + "MIDDLE_MARKER" + "B" * 300_000
    result = truncate_prompt(big, model_alias="llama-3", reserve_chars=0)
    assert len(result) < len(big)
    assert "MIDDLE_MARKER" not in result
    assert "truncated" in result
    assert result.startswith("A")
    assert result.endswith("B")
    assert "MIDDLE_MARKER" not in result


def test_truncate_warning_inserted():
    # llama context ~28k chars — a 500k char prompt must trigger truncation
    big = "X" * 500_000
    result = truncate_prompt(big, model_alias="llama-3", reserve_chars=0)
    assert "truncated" in result.lower()


def test_truncate_claude_context_limit_respected():
    # claude models typically have 200k token context; at 4 chars/token that's ~800k chars.
    # A 1M char prompt should definitely get truncated.
    big = "Z" * 1_000_000
    result = truncate_prompt(big, model_alias="claude-3-5-sonnet", reserve_chars=0)
    assert len(result) < len(big)


def test_needs_web_fallback_detects_marker():
    assert needs_web_fallback("TOOLS_UNAVAILABLE: cannot search") is True


def test_needs_web_fallback_case_insensitive():
    # re.IGNORECASE — lowercase also matches
    assert needs_web_fallback("tools_unavailable: missing") is True


def test_needs_web_fallback_normal_output():
    assert needs_web_fallback("Here is the answer you asked for.") is False


def test_needs_web_fallback_empty():
    assert needs_web_fallback("") is False


def test_build_web_fallback_prompt_contains_original():
    original = "What is the weather in London?"
    search_result = "London weather: 18°C, partly cloudy"
    combined = build_web_fallback_prompt(original, search_result)
    assert original in combined
    assert search_result in combined


def test_build_web_fallback_prompt_caps_search_result():
    original = "query"
    long_result = "R" * 20_000
    combined = build_web_fallback_prompt(original, long_result)
    # Should not exceed ~9000 chars (8000 cap on search + overhead)
    assert len(combined) < 10_000


def test_build_web_fallback_prompt_structure():
    combined = build_web_fallback_prompt("question?", "answer here")
    # Should label the search section clearly
    assert "search" in combined.lower() or "web" in combined.lower() or "result" in combined.lower()
