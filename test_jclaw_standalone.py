#!/usr/bin/env python3
"""
J Claw Standalone Test
======================
Test J Claw prompt with a real llama-server using the lightweight local runner.
"""
from __future__ import annotations

import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

LLAMA_CPP_DIR = Path("P:/llama cpp/llama-b9534-bin-win-cuda-13.3-x64")
LLAMA_SERVER = LLAMA_CPP_DIR / "llama-server.exe"
MODEL_PATH = Path("P:/gguf models/Qwopus3.5-9B-Coder-MTP-Q6_K.gguf")
PORT = 19194
API_URL = f"http://127.0.0.1:{PORT}/v1"


def start_server() -> subprocess.Popen:
    proc = subprocess.Popen(
        [str(LLAMA_SERVER), "-m", str(MODEL_PATH), "--port", str(PORT), "-ngl", "99", "-c", "32768"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    for _ in range(60):
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{PORT}/health", timeout=2) as r:
                if r.status == 200:
                    print("[J Claw Test] llama-server ready")
                    return proc
        except Exception:
            pass
        time.sleep(1)
    raise RuntimeError("llama-server failed to start")


def test_prompt():
    print("\n[J Claw Test] Running jclaw prompt...")
    env = os.environ.copy()
    env["JCLAW_GATEWAY_BASE_URL"] = API_URL
    env["ANTHROPIC_BASE_URL"] = API_URL
    env["ANTHROPIC_API_KEY"] = "dummy"
    env["JCLAW_MODEL"] = "jclaw-main"
    env["JCLAW_WORKER_MODEL"] = "jclaw-main"
    env["JCLAW_USE_LOCAL_RUNNER"] = "true"
    env["CONTAINER_TIMEOUT"] = "60000"
    env["IDLE_TIMEOUT"] = "60000"

    cmd = [
        sys.executable, "-m", "src.main", "prompt", "--timeout", "60",
        "Write a one-line Python script that prints 'JCLAW_OK'. Output only the code.",
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=90, cwd="P:/local_ai_agents/jclaw", env=env)
    output = result.stdout + result.stderr
    print(f"[J Claw Test] Output:\n{output[:1500]}")
    if "JCLAW_OK" in output or (result.returncode == 0 and output.strip()):
        print("[J Claw Test] PASS")
        return True
    print("[J Claw Test] FAIL")
    return False


def main():
    print("=" * 60)
    print("J CLAW STANDALONE TEST")
    print("=" * 60)
    proc = start_server()
    try:
        success = test_prompt()
        print("\n" + "=" * 60)
        print("J CLAW STANDALONE TEST:", "PASSED" if success else "FAILED")
        print("=" * 60)
    finally:
        proc.terminate()
        proc.wait(timeout=10)


if __name__ == "__main__":
    main()
