"""
Auto-configure J Claw .env from discovered local resources.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path


def discover_llama_cpp() -> str | None:
    roots = [
        Path("P:/llama cpp"),
        Path("P:/llama.cpp"),
        Path("C:/llama cpp"),
        Path("C:/llama.cpp"),
    ]
    found = []
    for root in roots:
        if not root.exists():
            continue
        try:
            for name in os.listdir(root):
                child = root / name
                if child.is_dir() and name.startswith("llama-"):
                    m = re.search(r'llama-b(\d+)', name)
                    build = int(m.group(1)) if m else 0
                    if (child / "llama-server.exe").exists():
                        found.append((build, str(child)))
        except Exception:
            pass
    if found:
        found.sort(key=lambda x: -x[0])
        return found[0][1]
    return None


def discover_gguf() -> str | None:
    roots = [
        Path("P:/gguf models"),
        Path.home() / "gguf models",
        Path.home() / "models",
    ]
    for root in roots:
        if not root.exists():
            continue
        try:
            for name in os.listdir(root):
                if name.lower().endswith(".gguf"):
                    return str(root / name)
        except Exception:
            pass
    return None


def main():
    env_path = Path(".env")
    existing = env_path.read_text(encoding="utf-8") if env_path.exists() else ""

    llama_dir = discover_llama_cpp()
    model_path = discover_gguf()

    lines = existing.splitlines() if existing else []
    new_lines = []
    replaced = {"llama": False, "model": False, "url": False}

    for line in lines:
        if llama_dir and line.startswith("JCLAW_LLAMA_SERVER_PATH="):
            new_lines.append(f"JCLAW_LLAMA_SERVER_PATH={llama_dir}\\llama-server.exe")
            replaced["llama"] = True
        elif model_path and line.startswith("JCLAW_MAIN_MODEL_PATH="):
            new_lines.append(f"JCLAW_MAIN_MODEL_PATH={model_path}")
            replaced["model"] = True
        elif line.startswith("JCLAW_GATEWAY_BASE_URL="):
            new_lines.append("JCLAW_GATEWAY_BASE_URL=http://127.0.0.1:8080/v1")
            replaced["url"] = True
        else:
            new_lines.append(line)

    # Append missing keys
    if llama_dir and not replaced["llama"]:
        new_lines.append(f"JCLAW_LLAMA_SERVER_PATH={llama_dir}\\llama-server.exe")
    if model_path and not replaced["model"]:
        new_lines.append(f"JCLAW_MAIN_MODEL_PATH={model_path}")
    if not replaced["url"]:
        new_lines.append("JCLAW_GATEWAY_BASE_URL=http://127.0.0.1:8080/v1")

    env_path.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
    print(f"Updated {env_path}")
    if llama_dir:
        print(f"  llama.cpp: {llama_dir}")
    if model_path:
        print(f"  Model:     {model_path}")


if __name__ == "__main__":
    main()
