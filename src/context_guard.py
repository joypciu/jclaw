"""
Context window guardrails for J Claw.

Before the agent runner sends a prompt, we check that the combined size of
the prompt + any tool payloads fits within the model's effective context window.
If it doesn't, we truncate from the middle (preserving the task instruction at
the start and the most recent context at the end — the oh-my-pi approach).

Web search fallback (Hermes pattern):
  When the agent returns TOOLS_UNAVAILABLE for WebSearch/WebFetch, we re-run
  the original query through the host browser and inject the result into a
  follow-up prompt.
"""
from __future__ import annotations

import logging
import re
from typing import Optional

logger = logging.getLogger(__name__)

# Conservative token-to-char ratio for typical English/code mixed content
_CHARS_PER_TOKEN = 3.5

# Default context caps by model family (chars, not tokens)
_DEFAULT_CONTEXT_CHARS = {
    "claude": int(200_000 * _CHARS_PER_TOKEN),   # Claude 3.x: 200K tokens
    "llama":  int(128_000 * _CHARS_PER_TOKEN),   # modern llama / qwen / local models
    "qwen":   int(128_000 * _CHARS_PER_TOKEN),   # Qwen2.5/Qwen3 support 128K
    "default": int(128_000 * _CHARS_PER_TOKEN),
}

# Reserve this many chars for the model's own response
_RESPONSE_RESERVE_CHARS = int(4_096 * _CHARS_PER_TOKEN)

_TOOLS_UNAVAILABLE_RE = re.compile(
    r"TOOLS_UNAVAILABLE|tool[s]?\s+(?:are\s+)?(?:not\s+)?unavailable",
    re.IGNORECASE,
)


def _detect_context_limit(model_alias: Optional[str]) -> int:
    if not model_alias:
        return _DEFAULT_CONTEXT_CHARS["default"]
    lower = model_alias.lower()
    for family, limit in _DEFAULT_CONTEXT_CHARS.items():
        if family in lower:
            return limit
    return _DEFAULT_CONTEXT_CHARS["default"]


def truncate_prompt(
    prompt: str,
    model_alias: Optional[str] = None,
    reserve_chars: int = _RESPONSE_RESERVE_CHARS,
) -> str:
    """
    Truncate prompt to fit context window using oh-my-pi's middle-out strategy:
    keep the start (task instruction) and end (recent context), drop the middle.

    Returns the prompt unchanged if it already fits.
    """
    limit = _detect_context_limit(model_alias) - reserve_chars
    if len(prompt) <= limit:
        return prompt

    keep_start = limit // 2
    keep_end = limit - keep_start
    dropped = len(prompt) - limit
    warning = (
        f"\n\n[...{dropped:,} characters truncated to fit context window...]\n\n"
    )
    truncated = prompt[:keep_start] + warning + prompt[-keep_end:]
    logger.warning(
        "Prompt truncated: original=%d chars, limit=%d chars, dropped=%d",
        len(prompt), limit, dropped,
    )
    return truncated


def needs_web_fallback(agent_result: str) -> bool:
    """Return True if the agent signalled that web tools were unavailable."""
    return bool(_TOOLS_UNAVAILABLE_RE.search(agent_result))


async def web_search_fallback(query: str) -> Optional[str]:
    """
    Fallback web search using the host browser MCP tool path.

    In production this is called from container_runner after detecting
    TOOLS_UNAVAILABLE in the agent result.  We import host_browser lazily
    to avoid circular imports in environments that don't have playwright.
    """
    try:
        from .host_browser import search_google_text  # type: ignore[import]
        result = await search_google_text(query)
        logger.info("web_search_fallback: got %d chars for query=%r", len(result or ""), query)
        return result
    except ImportError:
        logger.warning("web_search_fallback: host_browser not available (playwright not installed)")
        return None
    except Exception as exc:
        logger.warning("web_search_fallback: failed — %s", exc)
        return None


def build_web_fallback_prompt(original_prompt: str, search_result: str) -> str:
    """Wrap original prompt + browser search result into a retry prompt."""
    return (
        f"{original_prompt}\n\n"
        "--- Web Search Result (retrieved via host browser) ---\n"
        f"{search_result[:8000]}\n"
        "--- End of Web Search Result ---\n\n"
        "Use the above search result to answer the original request."
    )
