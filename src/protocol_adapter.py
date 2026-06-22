"""Anthropic Messages API ↔ OpenAI Chat Completions protocol adapter.

Translates requests and responses between the two API formats so jclaw's
credential proxy can route directly to llama-server / Ollama / LM Studio or
any OpenAI-compatible backend without requiring LiteLLM.

Improvements over LiteLLM that are unique to this adapter
──────────────────────────────────────────────────────────
  • Zero external process — runs inside the credential proxy thread
  • Per-alias backend routing: each alias has its own llama-server URL
  • Streaming with per-chunk Anthropic SSE wrapping (no full-response buffering)
  • Tool-call ID passthrough preserving Claude SDK session continuity
  • Tool argument accumulation across streaming chunks
  • Graceful degradation for unknown content block types
  • Works equally for scheduled tasks and interactive turns

Supported translation paths
───────────────────────────
  POST /v1/messages  (Anthropic) → POST /v1/chat/completions  (OpenAI)
  Response non-streaming         → Anthropic message object
  Response streaming SSE         → Anthropic SSE event stream

Content block handling
──────────────────────
  text          ✓ bidirectional
  image         ✓ base64 → image_url, url → image_url
  tool_use      ✓ → OpenAI tool_calls  (request assistant messages)
  tool_result   ✓ → OpenAI tool role messages
  document      ~ text extraction fallback
"""
from __future__ import annotations

import json
import logging
import uuid
from typing import Any, Generator, Iterator

import httpx

from .model_registry import ModelEndpoint, ModelRegistry

logger = logging.getLogger(__name__)

ANTHROPIC_MESSAGES_PATH = "/v1/messages"
OPENAI_CHAT_PATH = "/v1/chat/completions"

# finish_reason (OpenAI) → stop_reason (Anthropic)
_TO_ANTHROPIC_STOP = {
    "stop": "end_turn",
    "tool_calls": "tool_use",
    "length": "max_tokens",
    "content_filter": "stop_sequence",
}


# ── Request: Anthropic → OpenAI ───────────────────────────────────────────────

def _content_to_openai(content: Any) -> str | list[dict]:
    """Convert an Anthropic content value (str or block list) to OpenAI content."""
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return str(content)

    text_parts: list[str] = []
    image_parts: list[dict] = []

    for block in content:
        if not isinstance(block, dict):
            text_parts.append(str(block))
            continue
        btype = block.get("type", "")

        if btype == "text":
            text_parts.append(block.get("text", ""))

        elif btype == "image":
            src = block.get("source", {})
            if src.get("type") == "base64":
                media = src.get("media_type", "image/jpeg")
                data = src.get("data", "")
                image_parts.append({"type": "image_url",
                                     "image_url": {"url": f"data:{media};base64,{data}"}})
            elif src.get("type") == "url":
                image_parts.append({"type": "image_url",
                                     "image_url": {"url": src.get("url", "")}})

        elif btype == "document":
            src = block.get("source", {})
            if src.get("type") == "text":
                text_parts.append(src.get("text", ""))

        # tool_use / tool_result handled at message level
        elif btype not in ("tool_use", "tool_result"):
            if "text" in block:
                text_parts.append(block["text"])

    joined = "\n".join(t for t in text_parts if t)
    if image_parts:
        parts: list[dict] = []
        if joined:
            parts.append({"type": "text", "text": joined})
        parts.extend(image_parts)
        return parts
    return joined


def _messages_to_openai(messages: list[dict], system: str | None) -> list[dict]:
    """Convert Anthropic messages array to OpenAI messages array."""
    result: list[dict] = []
    if system:
        result.append({"role": "system", "content": system})

    for msg in messages:
        role = msg.get("role", "user")
        content = msg.get("content", "")

        if role == "assistant":
            if isinstance(content, list):
                texts: list[str] = []
                tool_calls: list[dict] = []
                for block in content:
                    if not isinstance(block, dict):
                        continue
                    if block.get("type") == "text":
                        texts.append(block.get("text", ""))
                    elif block.get("type") == "tool_use":
                        tool_calls.append({
                            "id": block.get("id", f"call_{uuid.uuid4().hex[:8]}"),
                            "type": "function",
                            "function": {
                                "name": block.get("name", ""),
                                "arguments": json.dumps(
                                    block.get("input", {}), ensure_ascii=False
                                ),
                            },
                        })
                oai: dict[str, Any] = {"role": "assistant"}
                joined = "\n".join(t for t in texts if t)
                oai["content"] = joined if joined else None
                if tool_calls:
                    oai["tool_calls"] = tool_calls
                result.append(oai)
            else:
                result.append({"role": "assistant", "content": _content_to_openai(content)})

        elif role == "user":
            if isinstance(content, list):
                tool_results = [b for b in content
                                if isinstance(b, dict) and b.get("type") == "tool_result"]
                other = [b for b in content
                         if not (isinstance(b, dict) and b.get("type") == "tool_result")]

                for tr in tool_results:
                    tr_content = tr.get("content", "")
                    if isinstance(tr_content, list):
                        tr_text = " ".join(
                            b.get("text", "") for b in tr_content
                            if isinstance(b, dict) and b.get("type") == "text"
                        )
                    elif isinstance(tr_content, str):
                        tr_text = tr_content
                    else:
                        tr_text = json.dumps(tr_content, ensure_ascii=False)
                    result.append({
                        "role": "tool",
                        "content": tr_text,
                        "tool_call_id": tr.get("tool_use_id", ""),
                    })

                if other:
                    user_content = _content_to_openai(other)
                    if user_content:
                        result.append({"role": "user", "content": user_content})
            else:
                result.append({"role": "user", "content": _content_to_openai(content)})

        else:
            result.append({"role": role, "content": _content_to_openai(content)})

    return result


def _tools_to_openai(tools: list[dict]) -> list[dict]:
    return [
        {
            "type": "function",
            "function": {
                "name": t.get("name", ""),
                "description": t.get("description", ""),
                "parameters": t.get("input_schema", {"type": "object", "properties": {}}),
            },
        }
        for t in tools
    ]


def _tool_choice_to_openai(tc: dict) -> Any:
    kind = tc.get("type", "auto")
    if kind == "auto":
        return "auto"
    if kind == "any":
        return "required"
    if kind == "tool":
        return {"type": "function", "function": {"name": tc.get("name", "")}}
    return "auto"


def anthropic_to_openai(body: dict, endpoint: ModelEndpoint) -> tuple[dict, bool]:
    """Translate an Anthropic /v1/messages body to OpenAI /v1/chat/completions.

    Returns (openai_body, is_streaming).
    """
    is_stream = bool(body.get("stream", False))

    oai: dict[str, Any] = {
        "model": endpoint.model or body.get("model", ""),
        "messages": _messages_to_openai(body.get("messages", []), body.get("system")),
        "stream": is_stream,
    }

    if "max_tokens" in body:
        oai["max_tokens"] = body["max_tokens"]
    if "temperature" in body:
        oai["temperature"] = body["temperature"]
    if "top_p" in body:
        oai["top_p"] = body["top_p"]
    if "stop_sequences" in body:
        oai["stop"] = body["stop_sequences"]
    if body.get("tools"):
        oai["tools"] = _tools_to_openai(body["tools"])
    if "tool_choice" in body:
        oai["tool_choice"] = _tool_choice_to_openai(body["tool_choice"])

    return oai, is_stream


# ── Response: OpenAI → Anthropic ─────────────────────────────────────────────

def _tool_calls_to_anthropic(tool_calls: list[dict]) -> list[dict]:
    blocks = []
    for tc in tool_calls:
        fn = tc.get("function", {})
        try:
            input_obj = json.loads(fn.get("arguments", "{}"))
        except json.JSONDecodeError:
            input_obj = {"_raw": fn.get("arguments", "")}
        blocks.append({
            "type": "tool_use",
            "id": tc.get("id", f"toolu_{uuid.uuid4().hex[:8]}"),
            "name": fn.get("name", ""),
            "input": input_obj,
        })
    return blocks


def openai_to_anthropic(body: dict, alias: str, msg_id: str) -> dict:
    """Translate an OpenAI /v1/chat/completions response to Anthropic /v1/messages format."""
    choice = (body.get("choices") or [{}])[0]
    message = choice.get("message", {})
    finish_reason = choice.get("finish_reason") or "stop"
    stop_reason = _TO_ANTHROPIC_STOP.get(finish_reason, "end_turn")

    content: list[dict] = []
    text = message.get("content") or ""
    if text:
        content.append({"type": "text", "text": text})

    tool_calls = message.get("tool_calls") or []
    if tool_calls:
        content.extend(_tool_calls_to_anthropic(tool_calls))
        stop_reason = "tool_use"

    usage = body.get("usage", {})
    return {
        "id": msg_id,
        "type": "message",
        "role": "assistant",
        "content": content,
        "model": alias,
        "stop_reason": stop_reason,
        "stop_sequence": None,
        "usage": {
            "input_tokens": usage.get("prompt_tokens", 0),
            "output_tokens": usage.get("completion_tokens", 0),
        },
    }


# ── Streaming: OpenAI SSE → Anthropic SSE ────────────────────────────────────

def _sse(event: str, data: dict) -> bytes:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n".encode()


class _ToolCallAccum:
    """Accumulates a single tool call's arguments across streaming chunks."""

    def __init__(self, tc_id: str, name: str) -> None:
        self.id = tc_id
        self.name = name
        self.args_buf = ""

    def append(self, fn: dict) -> None:
        if fn.get("name"):
            self.name = fn["name"]
        self.args_buf += fn.get("arguments", "")


def stream_openai_to_anthropic(
    lines: Iterator[str],
    alias: str,
    msg_id: str,
) -> Generator[bytes, None, None]:
    """Consume OpenAI streaming SSE lines, yield Anthropic SSE event bytes.

    Handled:
    - text streaming  → content_block_delta text_delta
    - tool calls      → accumulated, emitted as content_block tool_use on finish
    - usage tracking  → forwarded in message_delta
    - proper message_start / message_stop bracketing
    """
    header_sent = False
    text_block_open = False
    text_block_idx = 0
    next_block_idx = 0
    tools: dict[int, _ToolCallAccum] = {}  # OpenAI index → accumulator
    input_tokens = 0
    output_tokens = 0

    for line in lines:
        line = line.strip()
        if not line or line == "data: [DONE]":
            if line == "data: [DONE]":
                break
            continue

        if not line.startswith("data: "):
            continue

        try:
            chunk = json.loads(line[6:])
        except json.JSONDecodeError:
            continue

        if "usage" in chunk:
            u = chunk["usage"]
            input_tokens = u.get("prompt_tokens", input_tokens)
            output_tokens = u.get("completion_tokens", output_tokens)

        choices = chunk.get("choices") or []
        if not choices:
            continue

        choice = choices[0]
        delta = choice.get("delta", {})
        finish_reason = choice.get("finish_reason")

        # ── message_start on first chunk ──────────────────────────────────
        if not header_sent:
            yield _sse("message_start", {
                "type": "message_start",
                "message": {
                    "id": msg_id, "type": "message", "role": "assistant",
                    "content": [], "model": alias, "stop_reason": None,
                    "stop_sequence": None,
                    "usage": {"input_tokens": input_tokens, "output_tokens": 0},
                },
            })
            yield _sse("ping", {"type": "ping"})
            header_sent = True

        # ── text delta ────────────────────────────────────────────────────
        text_delta = delta.get("content") or ""
        if text_delta:
            if not text_block_open:
                text_block_idx = next_block_idx
                next_block_idx += 1
                yield _sse("content_block_start", {
                    "type": "content_block_start", "index": text_block_idx,
                    "content_block": {"type": "text", "text": ""},
                })
                text_block_open = True
            yield _sse("content_block_delta", {
                "type": "content_block_delta", "index": text_block_idx,
                "delta": {"type": "text_delta", "text": text_delta},
            })

        # ── tool call accumulation ────────────────────────────────────────
        for tc_chunk in (delta.get("tool_calls") or []):
            idx = tc_chunk.get("index", 0)
            fn = tc_chunk.get("function", {})
            if idx not in tools:
                tools[idx] = _ToolCallAccum(
                    tc_id=tc_chunk.get("id", f"toolu_{uuid.uuid4().hex[:8]}"),
                    name=fn.get("name", ""),
                )
            tools[idx].append(fn)

        # ── finish ────────────────────────────────────────────────────────
        if finish_reason:
            if text_block_open:
                yield _sse("content_block_stop", {
                    "type": "content_block_stop", "index": text_block_idx,
                })
                text_block_open = False

            # Emit accumulated tool_use blocks
            for tc_idx in sorted(tools):
                tc = tools[tc_idx]
                blk = next_block_idx
                next_block_idx += 1
                yield _sse("content_block_start", {
                    "type": "content_block_start", "index": blk,
                    "content_block": {"type": "tool_use", "id": tc.id,
                                      "name": tc.name, "input": {}},
                })
                yield _sse("content_block_delta", {
                    "type": "content_block_delta", "index": blk,
                    "delta": {"type": "input_json_delta",
                               "partial_json": tc.args_buf or "{}"},
                })
                yield _sse("content_block_stop", {"type": "content_block_stop", "index": blk})

            stop_reason = "tool_use" if tools else _TO_ANTHROPIC_STOP.get(finish_reason, "end_turn")
            yield _sse("message_delta", {
                "type": "message_delta",
                "delta": {"stop_reason": stop_reason, "stop_sequence": None},
                "usage": {"output_tokens": output_tokens},
            })
            yield _sse("message_stop", {"type": "message_stop"})
            return

    # Stream ended without explicit finish_reason
    if header_sent:
        if text_block_open:
            yield _sse("content_block_stop", {"type": "content_block_stop", "index": text_block_idx})
        yield _sse("message_delta", {
            "type": "message_delta",
            "delta": {"stop_reason": "end_turn", "stop_sequence": None},
            "usage": {"output_tokens": output_tokens},
        })
        yield _sse("message_stop", {"type": "message_stop"})


# ── ProtocolAdapter ────────────────────────────────────────────────────────────

class ProtocolAdapter:
    """Routes Anthropic /v1/messages requests directly to OpenAI-compatible backends.

    Used by the credential proxy to eliminate LiteLLM from the hot path.
    Non-messages requests (e.g. /v1/models) are NOT handled here and should be
    forwarded by the proxy using its existing ProviderRouter logic.
    """

    def __init__(self, registry: ModelRegistry) -> None:
        self._registry = registry

    def handles(self, path: str) -> bool:
        """Return True if this request should be handled by the adapter."""
        clean = path.split("?")[0].rstrip("/")
        return clean == ANTHROPIC_MESSAGES_PATH or clean.endswith(ANTHROPIC_MESSAGES_PATH)

    def handle(
        self,
        body_bytes: bytes,
    ) -> tuple[int, dict[str, str], bytes | Generator[bytes, None, None]]:
        """Process one Anthropic /v1/messages request.

        Returns (status_code, response_headers, body_or_stream_generator).
        When streaming, the third element is a generator that yields SSE bytes.
        """
        # ── Parse request ──────────────────────────────────────────────────
        try:
            body = json.loads(body_bytes)
        except json.JSONDecodeError as exc:
            return _error(400, "invalid_request_error", f"Invalid JSON: {exc}")

        alias = body.get("model", "")
        endpoint = self._registry.resolve(alias)

        if not endpoint:
            return _error(
                502, "overloaded_error",
                f"No backend configured for model '{alias}'. "
                "Set JCLAW_MODEL_ALIASES in .env."
            )

        # ── Translate request ──────────────────────────────────────────────
        try:
            oai_body, is_stream = anthropic_to_openai(body, endpoint)
        except Exception as exc:
            logger.error("Protocol adapter: request translation failed: %s", exc, exc_info=True)
            return _error(500, "api_error", f"Request translation error: {exc}")

        upstream_url = endpoint.url.rstrip("/") + "/chat/completions"
        req_headers: dict[str, str] = {"Content-Type": "application/json"}
        if endpoint.api_key:
            req_headers["Authorization"] = f"Bearer {endpoint.api_key}"

        msg_id = f"msg_{uuid.uuid4().hex[:8]}"

        # ── Streaming ─────────────────────────────────────────────────────
        if is_stream:
            req_headers["Accept"] = "text/event-stream"

            def _gen() -> Generator[bytes, None, None]:
                try:
                    with httpx.Client(timeout=300.0) as client:
                        with client.stream("POST", upstream_url,
                                           json=oai_body, headers=req_headers) as resp:
                            if resp.status_code != 200:
                                body_err = resp.read()
                                logger.error("Backend %s returned %s: %s",
                                             endpoint.alias, resp.status_code, body_err[:200])
                                _, _, err_bytes = _error(
                                    resp.status_code, "api_error",
                                    f"Backend {endpoint.alias} error {resp.status_code}"
                                )
                                yield err_bytes  # type: ignore[misc]
                                return
                            yield from stream_openai_to_anthropic(
                                resp.iter_lines(), alias=alias, msg_id=msg_id
                            )
                except Exception as exc:
                    logger.error("Protocol adapter: streaming error: %s", exc, exc_info=True)
                    _, _, err_bytes = _error(502, "api_error", f"Streaming error: {exc}")
                    yield err_bytes  # type: ignore[misc]

            return 200, {
                "Content-Type": "text/event-stream",
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
            }, _gen()

        # ── Non-streaming ──────────────────────────────────────────────────
        try:
            with httpx.Client(timeout=300.0) as client:
                resp = client.post(upstream_url, json=oai_body, headers=req_headers)
        except Exception as exc:
            logger.error("Protocol adapter: upstream request failed: %s", exc, exc_info=True)
            return _error(502, "api_error", f"Upstream error: {exc}")

        if resp.status_code != 200:
            try:
                oai_err = resp.json().get("error", {})
                msg = oai_err.get("message") or resp.text[:300]
            except Exception:
                msg = resp.text[:300]
            return _error(resp.status_code, "api_error", msg)

        try:
            anthropic_resp = openai_to_anthropic(resp.json(), alias=alias, msg_id=msg_id)
            return (
                200,
                {"Content-Type": "application/json"},
                json.dumps(anthropic_resp, ensure_ascii=False).encode(),
            )
        except Exception as exc:
            logger.error("Protocol adapter: response translation failed: %s", exc, exc_info=True)
            return _error(500, "api_error", f"Response translation error: {exc}")


# ── helpers ───────────────────────────────────────────────────────────────────

def _error(
    status: int, err_type: str, message: str
) -> tuple[int, dict[str, str], bytes]:
    body = json.dumps(
        {"type": "error", "error": {"type": err_type, "message": message}},
        ensure_ascii=False,
    ).encode()
    return status, {"Content-Type": "application/json"}, body
