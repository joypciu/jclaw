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
import time
import urllib.request

OUTPUT_START_MARKER = "---JCLAW_OUTPUT_START---"
OUTPUT_END_MARKER = "---JCLAW_OUTPUT_END---"


def _log(msg: str) -> None:
    sys.stderr.write(f"[local-runner] {msg}\n")
    sys.stderr.flush()


def _resolve_endpoint(model: str) -> tuple[str, str]:
    """Return (base_url, api_key) honoring J Claw aliases and env vars."""
    base_url = (
        os.environ.get("JCLAW_GATEWAY_BASE_URL")
        or os.environ.get("LLAMACPP_BASE_URL")
        or os.environ.get("ANTHROPIC_BASE_URL", "http://127.0.0.1:8080/v1")
    )
    api_key = os.environ.get("ANTHROPIC_API_KEY", "dummy")

    try:
        sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        from model_registry import load_model_registry
        reg = load_model_registry()
        ep = reg.resolve(model)
        if ep:
            base_url = ep.url.rstrip("/")
            api_key = ep.api_key or api_key
    except Exception:
        pass

    return base_url, api_key


def _call_completions(prompt: str, base_url: str, api_key: str, model: str, max_tokens: int, temperature: float) -> str:
    url = f"{base_url}/completions"
    data = json.dumps({
        "model": model,
        "prompt": prompt,
        "max_tokens": max_tokens,
        "temperature": temperature,
        "stop": ["<|im_end|>", "<|endoftext|>", "</s>"],
    }).encode("utf-8")

    req = urllib.request.Request(
        url,
        data=data,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
    )

    with urllib.request.urlopen(req, timeout=120.0) as resp:
        result = json.loads(resp.read().decode("utf-8"))
        return result["choices"][0]["text"]


def _call_local_llm(prompt: str) -> str:
    """Call the local API with retries and quality tweaks."""
    model = os.environ.get("JCLAW_MODEL", "local-model")
    base_url, api_key = _resolve_endpoint(model)
    max_tokens = int(os.environ.get("JCLAW_LOCAL_RUNNER_MAX_TOKENS", "4096"))
    retries = int(os.environ.get("JCLAW_LOCAL_RUNNER_RETRIES", "2"))

    # Wrap user prompt with concise instruction header
    wrapped = (
        "You are a concise coding assistant. Follow the user's instructions exactly. "
        "Output only what was requested, no extra commentary.\n\n"
        f"{prompt}\n"
    )

    last_error = ""
    for attempt in range(retries + 1):
        if attempt > 0:
            _log(f"Retry {attempt}/{retries} after empty/bad result")
            time.sleep(1.5)

        try:
            # Lower temperature on retries for more deterministic output
            temperature = 0.7 if attempt == 0 else 0.2
            text = _call_completions(wrapped, base_url, api_key, model, max_tokens, temperature)
            if text and text.strip():
                return text
            _log("Model returned empty result")
        except Exception as e:
            last_error = str(e)
            _log(f"API call failed: {e}")

    return f"[Local LLM error: empty result after {retries + 1} attempts. Last error: {last_error}]"


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
