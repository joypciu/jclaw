"""Model alias registry for jclaw's self-contained credential proxy.

Maps logical model aliases (e.g. "jclaw-main") to concrete backend endpoints
so the credential proxy can route directly to llama-server / Ollama / LM Studio
without going through LiteLLM.

Configuration sources (in priority order):
  1. JCLAW_MODEL_ALIASES  — JSON dict:
       {"jclaw-main": {"url": "http://127.0.0.1:5002/v1", "model": "Qwen.gguf", "key": "x"}}
  2. Per-alias env vars   — JCLAW_ALIAS_<NAME_UPPER>_URL / _MODEL / _KEY
       JCLAW_ALIAS_JCLAW_MAIN_URL=http://127.0.0.1:5002/v1
       JCLAW_ALIAS_JCLAW_MAIN_MODEL=Qwen_Qwen3.5-2B-bf16.gguf
  3. Single gateway fallback — JCLAW_GATEWAY_BASE_URL (pass-through, no translation)
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from typing import Optional

from .env import read_env_file

logger = logging.getLogger(__name__)

_REGISTRY_ENV_KEYS = [
    "JCLAW_MODEL_ALIASES",
    "JCLAW_GATEWAY_BASE_URL",
    "JCLAW_GATEWAY_API_KEY",
    "JCLAW_GATEWAY_AUTH_TOKEN",
    "LOCAL_MODEL_BASE_URL",
    "LOCAL_MODEL_AUTH_TOKEN",
]

# Wildcard alias key — used when only a single gateway is configured
_WILDCARD = "*"


@dataclass
class ModelEndpoint:
    """A resolved backend endpoint for a model alias."""

    alias: str
    url: str       # base URL, e.g. http://127.0.0.1:5002/v1  (no trailing slash)
    model: str     # model name sent to the backend (empty → use alias as-is)
    api_key: str = ""


class ModelRegistry:
    """Maps model alias names to backend endpoints."""

    def __init__(self, endpoints: list[ModelEndpoint]) -> None:
        self._map: dict[str, ModelEndpoint] = {e.alias: e for e in endpoints}

    def resolve(self, alias: str) -> Optional[ModelEndpoint]:
        """Return the endpoint for *alias*, or the wildcard fallback if present."""
        return self._map.get(alias) or self._map.get(_WILDCARD)

    def is_known_alias(self, model: str) -> bool:
        """True when *model* is a registered alias (not a raw model name)."""
        return model in self._map or _WILDCARD in self._map

    def all_aliases(self) -> list[str]:
        return [k for k in self._map if k != _WILDCARD]

    def __repr__(self) -> str:  # pragma: no cover
        return f"ModelRegistry({self.all_aliases()})"


def load_model_registry() -> ModelRegistry:
    """Build a ModelRegistry from the current environment / .env file."""
    extra_keys = [k for k in os.environ if k.startswith("JCLAW_ALIAS_")]
    env = read_env_file(_REGISTRY_ENV_KEYS + extra_keys)
    endpoints: list[ModelEndpoint] = []

    # ── Source 1: JCLAW_MODEL_ALIASES JSON ──────────────────────────────────
    aliases_json = os.environ.get("JCLAW_MODEL_ALIASES") or env.get("JCLAW_MODEL_ALIASES", "")
    if aliases_json:
        try:
            parsed = json.loads(aliases_json)
            for alias, cfg in parsed.items():
                if not isinstance(cfg, dict):
                    continue
                url = (cfg.get("url") or "").rstrip("/")
                model = cfg.get("model") or ""
                key = cfg.get("key") or cfg.get("api_key") or ""
                if url:
                    endpoints.append(ModelEndpoint(alias=alias, url=url, model=model, api_key=key))
        except (json.JSONDecodeError, AttributeError) as exc:
            logger.warning("ModelRegistry: failed to parse JCLAW_MODEL_ALIASES: %s", exc)

    # ── Source 2: per-alias env vars JCLAW_ALIAS_<NAME>_URL ─────────────────
    seen = {e.alias for e in endpoints}
    for key in list(os.environ) + list(env):
        if key.startswith("JCLAW_ALIAS_") and key.endswith("_URL"):
            suffix = key[len("JCLAW_ALIAS_"):-len("_URL")]   # e.g. JCLAW_MAIN
            alias = suffix.lower().replace("_", "-")           # jclaw-main
            if alias in seen:
                continue
            url = (os.environ.get(key) or env.get(key, "")).rstrip("/")
            model_key = f"JCLAW_ALIAS_{suffix}_MODEL"
            key_key = f"JCLAW_ALIAS_{suffix}_KEY"
            model = os.environ.get(model_key) or env.get(model_key, "")
            api_key = os.environ.get(key_key) or env.get(key_key, "")
            if url:
                endpoints.append(ModelEndpoint(alias=alias, url=url, model=model, api_key=api_key))
                seen.add(alias)

    # ── Source 3: single gateway fallback ────────────────────────────────────
    if not endpoints:
        fallback_url = (
            os.environ.get("JCLAW_GATEWAY_BASE_URL") or env.get("JCLAW_GATEWAY_BASE_URL", "")
            or os.environ.get("LOCAL_MODEL_BASE_URL") or env.get("LOCAL_MODEL_BASE_URL", "")
        ).rstrip("/")
        fallback_key = (
            os.environ.get("JCLAW_GATEWAY_API_KEY") or env.get("JCLAW_GATEWAY_API_KEY", "")
            or os.environ.get("JCLAW_GATEWAY_AUTH_TOKEN") or env.get("JCLAW_GATEWAY_AUTH_TOKEN", "")
            or os.environ.get("LOCAL_MODEL_AUTH_TOKEN") or env.get("LOCAL_MODEL_AUTH_TOKEN", "")
        )
        if fallback_url:
            endpoints.append(
                ModelEndpoint(alias=_WILDCARD, url=fallback_url, model="", api_key=fallback_key)
            )
            logger.info("ModelRegistry: no aliases configured — using single gateway %s", fallback_url)

    if endpoints:
        named = [e.alias for e in endpoints if e.alias != _WILDCARD]
        logger.info("ModelRegistry: loaded aliases=%s", named or ["(wildcard gateway)"])
    else:
        logger.warning("ModelRegistry: no backends configured — Anthropic cloud will be used")

    return ModelRegistry(endpoints)
