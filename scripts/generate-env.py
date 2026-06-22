"""
J Claw — Environment Template
==============================
Copy this to .env and customize for your setup.
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path


TEMPLATE = """# J Claw — Local AI Assistant Configuration
# Generated for your environment

ASSISTANT_NAME={assistant_name}
ASSISTANT_HAS_OWN_NUMBER=false

CONTAINER_IMAGE=nanoclaw-agent:latest
MAX_CONCURRENT_CONTAINERS={max_containers}

# ── Model aliases ──────────────────────────────────────────────────
# Point to your running llama.cpp server on port 8080
JCLAW_MODEL_ALIASES={{"jclaw-main":{{"url":"http://127.0.0.1:{llama_port}/v1","model":"local-model","key":"dummy-key"}}}}
JCLAW_MODEL=jclaw-main
JCLAW_WORKER_MODEL=jclaw-main
JCLAW_USE_WORKER_MODEL_FOR_SCHEDULED=true

# ── Model paths ───────────────────────────────────────────────────
JCLAW_LLAMA_SERVER_PATH={llama_server_path}
JCLAW_MAIN_MODEL_PATH={model_path}
JCLAW_LLAMA_MAIN_PORT={llama_port}
JCLAW_LLAMA_GPU_LAYERS={ngl}
JCLAW_LLAMA_CONTEXT={ctx}
JCLAW_LLAMA_HOST=127.0.0.1

# ── Multi-provider routing ─────────────────────────────────────────
JCLAW_ROUTER_STRATEGY=balanced
JCLAW_LOCAL_COST=0.01

# Direct llama.cpp access (already running)
LLAMACPP_BASE_URL=http://127.0.0.1:{llama_port}/v1

# OpenRouter (set key to enable)
# OPENROUTER_API_KEY=sk-or-v1-...

# OpenCode Zen (set key to enable)
# OPENCODE_API_KEY=oc-...

# Anthropic cloud (set key to enable)
# ANTHROPIC_API_KEY=sk-ant-...

# ── Browser automation (auto-detected) ─────────────────────────────
{chrome_line}
{firefox_line}
{opera_line}

# ── Timeouts ───────────────────────────────────────────────────────
CONTAINER_TIMEOUT=3600000
IDLE_TIMEOUT=3600000
JCLAW_HOST_BROWSER_HEADLESS=false
JCLAW_HOST_BROWSER_IDLE_MS=300000
"""


def generate_env(
    assistant_name: str = "JClaw",
    max_containers: int = 3,
    llama_port: int = 8080,
    llama_server_path: str = "",
    model_path: str = "",
    ngl: int = 99,
    ctx: int = 16384,
    chrome_path: str = "",
    firefox_path: str = "",
    opera_path: str = "",
) -> str:
    chrome_line = f"JCLAW_CHROME_PATH={chrome_path}" if chrome_path else "# JCLAW_CHROME_PATH= (not found)"
    firefox_line = f"JCLAW_FIREFOX_PATH={firefox_path}" if firefox_path else "# JCLAW_FIREFOX_PATH= (not found)"
    opera_line = f"JCLAW_OPERA_PATH={opera_path}" if opera_path else "# JCLAW_OPERA_PATH= (not found)"

    return TEMPLATE.format(
        assistant_name=assistant_name,
        max_containers=max_containers,
        llama_port=llama_port,
        llama_server_path=llama_server_path,
        model_path=model_path,
        ngl=ngl,
        ctx=ctx,
        chrome_line=chrome_line,
        firefox_line=firefox_line,
        opera_line=opera_line,
    )


def main():
    parser = argparse.ArgumentParser(description="Generate J Claw .env template")
    parser.add_argument("--output", default=".env", help="Output file path")
    parser.add_argument("--llama-port", type=int, default=8080)
    parser.add_argument("--llama-server-path", default="")
    parser.add_argument("--model-path", default="")
    parser.add_argument("--ngl", type=int, default=99)
    parser.add_argument("--ctx", type=int, default=16384)
    args = parser.parse_args()

    content = generate_env(
        llama_port=args.llama_port,
        llama_server_path=args.llama_server_path,
        model_path=args.model_path,
        ngl=args.ngl,
        ctx=args.ctx,
    )

    Path(args.output).write_text(content, encoding="utf-8")
    print(f"Generated {args.output}")


if __name__ == "__main__":
    main()
