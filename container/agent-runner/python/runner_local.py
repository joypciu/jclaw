"""
J Claw — Lightweight Local Agent Runner
=========================================
A minimal Python agent runner for J Claw that calls a local
OpenAI-compatible API directly.  Much faster than the full
TypeScript agent-runner for single-shot prompts.

Usage:
    python runner_local.py <input_json_path>
"""
from __future__ import annotations

import json
import os
import sys
import urllib.request

OUTPUT_START_MARKER = "---JCLAW_OUTPUT_START---"
OUTPUT_END_MARKER = "---JCLAW_OUTPUT_END---"


def _call_local_llm(prompt: str) -> str:
    """Call the local OpenAI-compatible API."""
    # Prefer explicit local API env vars, then fallback to ANTHROPIC_BASE_URL
    base_url = (
        os.environ.get("JCLAW_GATEWAY_BASE_URL")
        or os.environ.get("LLAMACPP_BASE_URL")
        or os.environ.get("ANTHROPIC_BASE_URL", "http://127.0.0.1:8080/v1")
    )
    api_key = os.environ.get("ANTHROPIC_API_KEY", "dummy")
    model = os.environ.get("JCLAW_MODEL", "local-model")

    # JCLAW_MODEL may be an alias; resolve via model registry if available
    try:
        sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        from model_registry import load_model_registry
        reg = load_model_registry()
        ep = reg.resolve(model)
        if ep:
            base_url = ep.url.rstrip("/")
            api_key = ep.api_key or api_key
            model = ep.model
    except Exception:
        pass

    # Use legacy /v1/completions endpoint — more compatible with local models
    # such as Qwopus/Qwen3.5 that may return empty chat-completion content.
    max_tokens = int(os.environ.get("JCLAW_LOCAL_RUNNER_MAX_TOKENS", "4096"))
    url = f"{base_url}/completions"
    data = json.dumps({
        "model": model,
        "prompt": prompt,
        "max_tokens": max_tokens,
        "temperature": 0.7,
        "stop": ["<|im_end|>", "<|endoftext|>"],
    }).encode("utf-8")

    req = urllib.request.Request(
        url,
        data=data,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
    )

    try:
        with urllib.request.urlopen(req, timeout=120.0) as resp:
            result = json.loads(resp.read().decode("utf-8"))
            return result["choices"][0]["text"]
    except Exception as e:
        return f"[Local LLM error: {e}]"


def _write_output(payload: dict) -> None:
    sys.stdout.write(OUTPUT_START_MARKER + "\n")
    sys.stdout.write(json.dumps(payload, ensure_ascii=True) + "\n")
    sys.stdout.write(OUTPUT_END_MARKER + "\n")
    sys.stdout.flush()


def main() -> int:
    input_path = sys.argv[1] if len(sys.argv) > 1 else None
    if not input_path:
        _write_output({"status": "error", "result": None, "error": "No input file path provided"})
        return 1

    try:
        raw = open(input_path, "r", encoding="utf-8").read()
        data = json.loads(raw) if raw.strip() else {}
    except Exception as exc:
        _write_output({"status": "error", "result": None, "error": f"Failed to read input: {exc}"})
        return 1

    prompt = str(data.get("prompt", ""))
    group = str(data.get("groupFolder", "unknown"))

    if not prompt:
        _write_output({"status": "success", "result": "", "newSessionId": None})
        return 0

    result = _call_local_llm(prompt)

    _write_output({
        "status": "success",
        "result": result,
        "newSessionId": None,
    })
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
