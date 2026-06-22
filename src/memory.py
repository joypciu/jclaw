"""
Hindsight Memory for J Claw — inspired by oh-my-pi's Hindsight system.

oh-my-pi's key insight: before the context window rolls over and old messages
are compacted away, extract the *key facts* from recent conversation and write
them into the agent's persistent memory (CLAUDE.md).  This means the agent
always "remembers" what happened even across session boundaries.

J Claw already archives full transcripts via the PreCompact hook in index.ts.
This module adds a *structured* layer on top:
  1. After each successful agent run, call `update_group_memory()`.
  2. It reads the last N lines of the group's conversation archive.
  3. It appends a dated "## Episode" block to the group's CLAUDE.md.
  4. It keeps the CLAUDE.md under MAX_MEMORY_CHARS by rotating old episodes out.

The memory format is designed so Claude Code can use it directly as context
when the agent resumes — no separate retrieval step needed.
"""
from __future__ import annotations

import logging
import re
import time
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# Maximum size of the memory section in CLAUDE.md (chars)
MAX_MEMORY_CHARS = 12_000

# Header / footer markers so we can surgically update only our section
_MEMORY_HEADER = "<!-- jclaw:memory:start -->"
_MEMORY_FOOTER = "<!-- jclaw:memory:end -->"

# How many recent archived lines to extract key facts from
_SUMMARY_MAX_CHARS = 6_000


def _iso_date() -> str:
    return time.strftime("%Y-%m-%d", time.gmtime())


def _extract_key_facts(conversation_text: str) -> str:
    """
    Lightweight structured fact extraction from a conversation.

    We don't call the LLM here — this runs synchronously after every agent
    turn.  Instead we extract a condensed skeleton:
      - The user's request (first user line)
      - The outcome (last assistant line or result block)
      - Any file paths or URLs mentioned

    oh-my-pi does a full LLM summarisation pass; we keep it cheap so it
    doesn't slow down every response.  The full transcript is always
    available in conversations/ for deep recall.
    """
    lines = [ln.strip() for ln in conversation_text.splitlines() if ln.strip()]
    if not lines:
        return ""

    user_lines = [ln for ln in lines if ln.startswith("**User**:") or ln.startswith("User:")]
    assistant_lines = [ln for ln in lines if ln.startswith("**Assistant**:") or ln.startswith("Assistant:")]

    first_request = user_lines[0][:200] if user_lines else lines[0][:200]
    last_reply = assistant_lines[-1][:300] if assistant_lines else lines[-1][:300]

    # Extract file paths and URLs mentioned anywhere
    paths = re.findall(r'(?:[A-Z]:\\|/[\w/.-]+\.[\w]+|https?://\S+)', conversation_text)
    paths_line = "  Refs: " + ", ".join(dict.fromkeys(paths[:8])) if paths else ""

    parts = [f"  Q: {first_request}", f"  A: {last_reply}"]
    if paths_line:
        parts.append(paths_line)
    return "\n".join(parts)


def _latest_conversation(group_dir: Path) -> Optional[str]:
    """Return the text of the most recently archived conversation, if any."""
    conv_dir = group_dir / "conversations"
    if not conv_dir.exists():
        return None
    files = sorted(conv_dir.glob("*.md"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not files:
        return None
    try:
        text = files[0].read_text(encoding="utf-8", errors="replace")
        return text[-_SUMMARY_MAX_CHARS:] if len(text) > _SUMMARY_MAX_CHARS else text
    except Exception:
        return None


def _read_claude_md(group_dir: Path) -> str:
    path = group_dir / "CLAUDE.md"
    if path.exists():
        return path.read_text(encoding="utf-8")
    return ""


def _write_claude_md(group_dir: Path, content: str) -> None:
    path = group_dir / "CLAUDE.md"
    path.write_text(content, encoding="utf-8")


def _rotate_memory_block(memory_block: str) -> str:
    """
    Keep the memory block under MAX_MEMORY_CHARS by dropping the oldest episodes.
    Episodes are separated by '## Episode' headings; we drop from the top.
    """
    if len(memory_block) <= MAX_MEMORY_CHARS:
        return memory_block

    episodes = re.split(r'(?=^## Episode)', memory_block, flags=re.MULTILINE)
    while len("\n".join(episodes)) > MAX_MEMORY_CHARS and len(episodes) > 1:
        episodes.pop(0)  # drop oldest
    return "\n".join(episodes)


def update_group_memory(group_dir: Path, result_summary: Optional[str] = None) -> None:
    """
    Append a new episode to the group's CLAUDE.md Hindsight memory section.

    Call this after a successful agent run.  It's a no-op if there's no
    conversation to summarise (first run, or archived conversations missing).

    Args:
        group_dir:      The group's workspace directory.
        result_summary: Optional short summary of what the agent did (from
                        ContainerOutput.result).  If None, extracted from
                        the latest archived conversation.
    """
    conv_text = _latest_conversation(group_dir)
    if not conv_text and not result_summary:
        return

    facts = _extract_key_facts(conv_text or "") if conv_text else ""
    date = _iso_date()

    episode_lines = [f"## Episode {date}"]
    if result_summary:
        episode_lines.append(f"  Result: {result_summary[:300]}")
    if facts:
        episode_lines.append(facts)
    episode_lines.append("")
    new_episode = "\n".join(episode_lines)

    # Read existing CLAUDE.md and surgically update our section
    existing = _read_claude_md(group_dir)

    start_idx = existing.find(_MEMORY_HEADER)
    end_idx = existing.find(_MEMORY_FOOTER)

    if start_idx >= 0 and end_idx > start_idx:
        # Section already exists — extract, append, rotate
        inner = existing[start_idx + len(_MEMORY_HEADER):end_idx]
        updated_inner = _rotate_memory_block(inner.strip() + "\n\n" + new_episode)
        new_content = (
            existing[:start_idx]
            + _MEMORY_HEADER + "\n"
            + updated_inner + "\n"
            + _MEMORY_FOOTER
            + existing[end_idx + len(_MEMORY_FOOTER):]
        )
    else:
        # First time — append section to end of file
        memory_section = (
            "\n\n" + _MEMORY_HEADER + "\n"
            "# Hindsight Memory\n"
            "_Key facts from recent conversations — auto-updated by J Claw._\n\n"
            + new_episode
            + _MEMORY_FOOTER + "\n"
        )
        new_content = existing.rstrip() + memory_section

    _write_claude_md(group_dir, new_content)
    logger.debug("Hindsight memory updated for group_dir=%s", group_dir)


def recall_memory(group_dir: Path) -> Optional[str]:
    """
    Return the current Hindsight memory block for a group, or None if empty.
    Used by the context builder to inject memory into the agent's system prompt.
    """
    existing = _read_claude_md(group_dir)
    start_idx = existing.find(_MEMORY_HEADER)
    end_idx = existing.find(_MEMORY_FOOTER)
    if start_idx < 0 or end_idx <= start_idx:
        return None
    inner = existing[start_idx + len(_MEMORY_HEADER):end_idx].strip()
    return inner if inner else None
