"""Tests for src/memory.py — Hindsight Memory system."""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from src.memory import (
    MAX_MEMORY_CHARS,
    _MEMORY_FOOTER,
    _MEMORY_HEADER,
    _extract_key_facts,
    _rotate_memory_block,
    recall_memory,
    update_group_memory,
)


# ---------------------------------------------------------------------------
# _extract_key_facts
# ---------------------------------------------------------------------------

def test_extract_key_facts_empty():
    assert _extract_key_facts("") == ""


def test_extract_key_facts_extracts_user_line():
    text = "User: Can you help me with Python?\nAssistant: Sure, here's how."
    facts = _extract_key_facts(text)
    assert "Can you help me with Python?" in facts


def test_extract_key_facts_extracts_last_assistant_line():
    text = (
        "User: Do something\n"
        "Assistant: First reply\n"
        "Assistant: Final reply with details\n"
    )
    facts = _extract_key_facts(text)
    assert "Final reply" in facts


def test_extract_key_facts_extracts_paths():
    text = "User: Check this\nAssistant: I wrote to C:\\projects\\foo.py and done."
    facts = _extract_key_facts(text)
    assert "C:\\projects\\foo.py" in facts


# ---------------------------------------------------------------------------
# _rotate_memory_block
# ---------------------------------------------------------------------------

def test_rotate_short_block_unchanged():
    block = "## Episode 2026-01-01\n  Q: short\n  A: answer\n"
    result = _rotate_memory_block(block)
    assert result == block


def test_rotate_oversized_block_drops_oldest():
    # Make an oversized block with 5 episodes
    episodes = []
    for i in range(5):
        # Each episode is ~3k chars
        episodes.append(f"## Episode 2026-0{i+1}-01\n" + "x" * 3000 + "\n")
    block = "\n".join(episodes)
    assert len(block) > MAX_MEMORY_CHARS

    rotated = _rotate_memory_block(block)
    assert len(rotated) <= MAX_MEMORY_CHARS
    # Oldest episode should be gone, newest should remain
    assert "2026-01-01" not in rotated
    assert "2026-05-01" in rotated


# ---------------------------------------------------------------------------
# update_group_memory + recall_memory
# ---------------------------------------------------------------------------

def test_update_creates_memory_section(tmp_path: Path):
    group_dir = tmp_path / "testgroup"
    group_dir.mkdir()
    (group_dir / "CLAUDE.md").write_text("# Instructions\nDo stuff.\n")

    update_group_memory(group_dir, result_summary="Wrote hello.py")

    content = (group_dir / "CLAUDE.md").read_text()
    assert _MEMORY_HEADER in content
    assert _MEMORY_FOOTER in content
    assert "Wrote hello.py" in content


def test_update_appends_episodes(tmp_path: Path):
    group_dir = tmp_path / "testgroup"
    group_dir.mkdir()

    update_group_memory(group_dir, result_summary="First run")
    update_group_memory(group_dir, result_summary="Second run")

    content = (group_dir / "CLAUDE.md").read_text()
    assert "First run" in content
    assert "Second run" in content
    # Should have two Episode headings
    assert content.count("## Episode") == 2


def test_recall_memory_returns_section(tmp_path: Path):
    group_dir = tmp_path / "testgroup"
    group_dir.mkdir()

    update_group_memory(group_dir, result_summary="Did something useful")
    memory = recall_memory(group_dir)

    assert memory is not None
    assert "Did something useful" in memory


def test_recall_memory_empty_when_no_section(tmp_path: Path):
    group_dir = tmp_path / "nogroup"
    group_dir.mkdir()
    (group_dir / "CLAUDE.md").write_text("# Instructions\n")

    assert recall_memory(group_dir) is None


def test_recall_memory_no_claude_md(tmp_path: Path):
    group_dir = tmp_path / "nogroup"
    group_dir.mkdir()

    assert recall_memory(group_dir) is None


def test_update_no_op_when_no_conversation(tmp_path: Path):
    """With no conversations/ dir and no result_summary, should not create file."""
    group_dir = tmp_path / "empty"
    group_dir.mkdir()

    update_group_memory(group_dir, result_summary=None)

    # CLAUDE.md should not be created if there's nothing to write
    assert not (group_dir / "CLAUDE.md").exists()


def test_memory_section_stays_under_max(tmp_path: Path):
    group_dir = tmp_path / "testgroup"
    group_dir.mkdir()

    # Write many episodes to trigger rotation
    for i in range(20):
        update_group_memory(group_dir, result_summary="x" * 800)

    content = (group_dir / "CLAUDE.md").read_text()
    start = content.find(_MEMORY_HEADER)
    end = content.find(_MEMORY_FOOTER)
    inner = content[start:end + len(_MEMORY_FOOTER)]
    assert len(inner) <= MAX_MEMORY_CHARS + 2000  # allow small overhead
