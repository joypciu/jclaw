"""
J Claw — Local LLM Health Check
================================
Quick diagnostic for local model endpoints (LiteLLM, KoboldCPP, Ollama, llama-server).
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time

import httpx


async def check_endpoint(name: str, url: str, headers: dict | None = None, timeout: float = 5.0) -> dict:
    start = time.monotonic()
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.get(url, headers=headers or {})
        elapsed_ms = (time.monotonic() - start) * 1000
        return {
            "name": name,
            "url": url,
            "reachable": resp.status_code in (200, 401, 403),
            "status_code": resp.status_code,
            "latency_ms": round(elapsed_ms, 1),
            "error": None,
        }
    except Exception as e:
        return {
            "name": name,
            "url": url,
            "reachable": False,
            "status_code": None,
            "latency_ms": None,
            "error": str(e),
        }


async def main():
    parser = argparse.ArgumentParser(description="Check local LLM endpoint health")
    parser.add_argument("--url", default="http://127.0.0.1:4000/v1/models", help="Endpoint URL")
    parser.add_argument("--key", default="dummy-key", help="API key / auth token")
    parser.add_argument("--timeout", type=float, default=5.0)
    args = parser.parse_args()

    headers = {"Authorization": f"Bearer {args.key}"} if args.key else {}

    print(f"Checking {args.url} ...")
    result = await check_endpoint("local-llm", args.url, headers, args.timeout)

    print(json.dumps(result, indent=2))
    sys.exit(0 if result["reachable"] else 1)


if __name__ == "__main__":
    asyncio.run(main())
