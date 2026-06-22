"""
Multi-provider model gateway with health-aware smart routing.

Merges patterns from openclaude's smart_router.py and claw-code's provider patterns.

Strategies (JCLAW_ROUTER_STRATEGY):
  latency  — always pick the fastest responding provider
  cost     — always pick the cheapest (local first)
  balanced — weighted combination of latency × cost (default)

Providers are pinged on startup; unhealthy ones back off 60s before retry.

Additional providers:
  Ollama  — set OLLAMA_BASE_URL + BIG_MODEL/SMALL_MODEL
  Local   — set JCLAW_GATEWAY_BASE_URL (LiteLLM, KoboldCPP, Atomic Chat, etc.)
  Anthropic — set ANTHROPIC_API_KEY or CLAUDE_CODE_OAUTH_TOKEN
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from dataclasses import dataclass, field
from typing import Literal, Optional

import httpx

from .env import read_env_file

logger = logging.getLogger(__name__)

GatewayAuthMode = Literal["api-key", "oauth"]
RouterStrategy = Literal["latency", "cost", "balanced"]

_UNHEALTHY_BACKOFF_S = 60.0
_EMA_ALPHA = 0.3

_GATEWAY_KEYS = [
    "JCLAW_GATEWAY_API_KEY",
    "JCLAW_GATEWAY_AUTH_TOKEN",
    "JCLAW_GATEWAY_BASE_URL",
    "ANTHROPIC_API_KEY",
    "CLAUDE_CODE_OAUTH_TOKEN",
    "ANTHROPIC_AUTH_TOKEN",
    "ANTHROPIC_BASE_URL",
    "LOCAL_MODEL_AUTH_TOKEN",
    "LOCAL_MODEL_BASE_URL",
    "JCLAW_ROUTER_STRATEGY",
    "JCLAW_LOCAL_COST",
    "OLLAMA_BASE_URL",
    "BIG_MODEL",
    "SMALL_MODEL",
    "OPENROUTER_API_KEY",
    "OPENROUTER_BASE_URL",
    "OPENCODE_API_KEY",
    "OPENCODE_BASE_URL",
    "LLAMACPP_BASE_URL",
    "LLAMACPP_API_KEY",
]


def _get(env: dict[str, str], *keys: str) -> Optional[str]:
    for k in keys:
        v = os.environ.get(k) or env.get(k)
        if v:
            return v
    return None


@dataclass
class ProviderConfig:
    name: str
    base_url: str
    cost_multiplier: float = 1.0
    api_key: Optional[str] = None
    auth_token: Optional[str] = None
    ping_url: Optional[str] = None
    big_model: Optional[str] = None
    small_model: Optional[str] = None


@dataclass
class _Health:
    ema_latency_ms: float = 200.0
    error_count: int = 0
    request_count: int = 0
    unhealthy_until: float = 0.0  # monotonic timestamp


@dataclass
class ModelGatewaySecrets:
    api_key: Optional[str] = None
    auth_token: Optional[str] = None
    base_url: Optional[str] = None


def read_model_gateway_secrets() -> ModelGatewaySecrets:
    env = read_env_file(_GATEWAY_KEYS)
    return ModelGatewaySecrets(
        api_key=_get(env, "JCLAW_GATEWAY_API_KEY", "ANTHROPIC_API_KEY"),
        auth_token=_get(env, "JCLAW_GATEWAY_AUTH_TOKEN", "CLAUDE_CODE_OAUTH_TOKEN",
                        "ANTHROPIC_AUTH_TOKEN", "LOCAL_MODEL_AUTH_TOKEN"),
        base_url=_get(env, "JCLAW_GATEWAY_BASE_URL", "ANTHROPIC_BASE_URL", "LOCAL_MODEL_BASE_URL"),
    )


def detect_gateway_auth_mode_from_secrets(secrets: ModelGatewaySecrets) -> GatewayAuthMode:
    return "api-key" if secrets.api_key else "oauth"


def detect_gateway_auth_mode() -> GatewayAuthMode:
    return detect_gateway_auth_mode_from_secrets(read_model_gateway_secrets())


class ProviderRouter:
    """
    Routes requests across multiple model providers using health-aware scoring.

    Incorporates openclaude SmartRouter patterns:
    - Async startup ping of all providers
    - EMA latency tracking
    - Error-rate based unhealthy backoff
    - Strategy-based selection (latency / cost / balanced)
    """

    def __init__(
        self,
        providers: list[ProviderConfig],
        strategy: RouterStrategy = "balanced",
    ) -> None:
        self._providers = providers
        self._strategy = strategy
        self._health: dict[str, _Health] = {p.name: _Health() for p in providers}
        self._initialized = False

    @property
    def provider_count(self) -> int:
        return len(self._providers)

    async def initialize(self) -> None:
        """Ping all providers concurrently on startup (openclaude pattern)."""
        logger.info("ProviderRouter: benchmarking providers...")
        await asyncio.gather(
            *[self._ping(p) for p in self._providers],
            return_exceptions=True,
        )
        available = [p for p in self._providers if self._is_healthy(p)]
        logger.info(
            f"ProviderRouter ready. Available: {[p.name for p in available]}"
        )
        if not available:
            logger.warning("ProviderRouter: no providers available — check credentials")
        self._initialized = True

    async def _ping(self, provider: ProviderConfig) -> None:
        ping_url = provider.ping_url or f"{provider.base_url.rstrip('/')}/v1/models"
        headers: dict[str, str] = {}
        if provider.api_key:
            headers["x-api-key"] = provider.api_key
        elif provider.auth_token:
            headers["Authorization"] = f"Bearer {provider.auth_token}"
        start = time.monotonic()
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get(ping_url, headers=headers)
                elapsed_ms = (time.monotonic() - start) * 1000
                h = self._health[provider.name]
                if resp.status_code in (200, 400, 401, 403):
                    h.ema_latency_ms = elapsed_ms
                    h.unhealthy_until = 0.0
                    logger.debug(f"Provider {provider.name}: reachable ({elapsed_ms:.0f}ms)")
                else:
                    h.unhealthy_until = time.monotonic() + _UNHEALTHY_BACKOFF_S
        except Exception as e:
            logger.warning(f"ProviderRouter: {provider.name} unreachable — {e}")
            self._health[provider.name].unhealthy_until = time.monotonic() + _UNHEALTHY_BACKOFF_S

    def _is_healthy(self, provider: ProviderConfig) -> bool:
        return self._health[provider.name].unhealthy_until <= time.monotonic()

    def select_provider(self) -> ProviderConfig:
        """Pick the best available provider based on health and strategy."""
        healthy = [p for p in self._providers if self._is_healthy(p)]
        candidates = healthy if healthy else self._providers  # fallback if all unhealthy
        if len(candidates) == 1:
            return candidates[0]
        return min(candidates, key=lambda p: self._score(p))

    def record_success(self, name: str, latency_ms: float) -> None:
        h = self._health.get(name)
        if not h:
            return
        h.request_count += 1
        h.ema_latency_ms = _EMA_ALPHA * latency_ms + (1 - _EMA_ALPHA) * h.ema_latency_ms
        h.unhealthy_until = 0.0

    def record_error(self, name: str) -> None:
        h = self._health.get(name)
        if not h:
            return
        h.error_count += 1
        h.request_count += 1
        h.unhealthy_until = time.monotonic() + _UNHEALTHY_BACKOFF_S
        # Schedule re-check after backoff (openclaude pattern)
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            # No running loop (for example: sync proxy server thread).
            # Provider becomes eligible again after unhealthy_until anyway.
            return
        loop.create_task(self._recheck_after(name, _UNHEALTHY_BACKOFF_S))

    async def _recheck_after(self, name: str, delay: float) -> None:
        await asyncio.sleep(delay)
        provider = next((p for p in self._providers if p.name == name), None)
        if provider:
            await self._ping(provider)

    def health_snapshot(self) -> list[dict]:
        now = time.monotonic()
        return [
            {
                "provider": p.name,
                "healthy": self._is_healthy(p),
                "ema_latency_ms": round(self._health[p.name].ema_latency_ms, 1),
                "error_count": self._health[p.name].error_count,
                "request_count": self._health[p.name].request_count,
                "cost_multiplier": p.cost_multiplier,
                "score": round(self._score(p), 4) if self._is_healthy(p) else "N/A",
            }
            for p in self._providers
        ]

    def _score(self, p: ProviderConfig) -> float:
        h = self._health[p.name]
        if self._strategy == "latency":
            return h.ema_latency_ms
        if self._strategy == "cost":
            return p.cost_multiplier
        # balanced (default)
        return h.ema_latency_ms * max(p.cost_multiplier, 0.001)


def build_provider_router() -> ProviderRouter:
    """
    Build a ProviderRouter from environment configuration.
    Supports: local gateway (LiteLLM/Kobold/Ollama/Atomic Chat) + Anthropic cloud.
    """
    env = read_env_file(_GATEWAY_KEYS)

    strategy: RouterStrategy = (
        os.environ.get("JCLAW_ROUTER_STRATEGY") or env.get("JCLAW_ROUTER_STRATEGY", "balanced")
    )  # type: ignore[assignment]
    local_cost = float(
        os.environ.get("JCLAW_LOCAL_COST") or env.get("JCLAW_LOCAL_COST", "0.01")
    )

    providers: list[ProviderConfig] = []

    # Ollama provider (openclaude pattern)
    ollama_url = _get(env, "OLLAMA_BASE_URL")
    if ollama_url:
        providers.append(ProviderConfig(
            name="ollama",
            base_url=ollama_url,
            cost_multiplier=0.0,
            ping_url=f"{ollama_url.rstrip('/')}/api/tags",
            big_model=_get(env, "BIG_MODEL"),
            small_model=_get(env, "SMALL_MODEL"),
        ))

    # llama.cpp server provider
    llamacpp_url = _get(env, "LLAMACPP_BASE_URL")
    llamacpp_key = _get(env, "LLAMACPP_API_KEY")
    if llamacpp_url:
        providers.append(ProviderConfig(
            name="llamacpp",
            base_url=llamacpp_url,
            cost_multiplier=0.0,
            api_key=llamacpp_key,
            ping_url=f"{llamacpp_url.rstrip('/')}/v1/models",
        ))

    # Local gateway provider (LiteLLM, KoboldCPP, Atomic Chat, etc.)
    local_url = _get(env, "JCLAW_GATEWAY_BASE_URL", "LOCAL_MODEL_BASE_URL")
    if local_url and local_url != ollama_url and local_url != llamacpp_url:
        providers.append(ProviderConfig(
            name="local",
            base_url=local_url,
            cost_multiplier=local_cost,
            api_key=_get(env, "JCLAW_GATEWAY_API_KEY", "LOCAL_MODEL_AUTH_TOKEN"),
            auth_token=_get(env, "JCLAW_GATEWAY_AUTH_TOKEN", "LOCAL_MODEL_AUTH_TOKEN"),
        ))

    # OpenRouter provider
    openrouter_key = _get(env, "OPENROUTER_API_KEY")
    openrouter_url = _get(env, "OPENROUTER_BASE_URL") or "https://openrouter.ai/api/v1"
    if openrouter_key:
        providers.append(ProviderConfig(
            name="openrouter",
            base_url=openrouter_url,
            cost_multiplier=0.3,
            api_key=openrouter_key,
            ping_url=f"{openrouter_url.rstrip('/')}/models",
        ))

    # OpenCode Zen provider
    opencode_key = _get(env, "OPENCODE_API_KEY")
    opencode_url = _get(env, "OPENCODE_BASE_URL") or "https://api.opencode.ai/v1"
    if opencode_key:
        providers.append(ProviderConfig(
            name="opencode",
            base_url=opencode_url,
            cost_multiplier=0.15,
            api_key=opencode_key,
            ping_url=f"{opencode_url.rstrip('/')}/models",
        ))

    # Anthropic cloud provider
    anthropic_key = _get(env, "ANTHROPIC_API_KEY")
    anthropic_token = _get(env, "CLAUDE_CODE_OAUTH_TOKEN", "ANTHROPIC_AUTH_TOKEN")
    anthropic_base = _get(env, "ANTHROPIC_BASE_URL") or "https://api.anthropic.com"

    if anthropic_key or anthropic_token or not providers:
        providers.append(ProviderConfig(
            name="anthropic",
            base_url=anthropic_base,
            cost_multiplier=1.0,
            api_key=anthropic_key,
            auth_token=anthropic_token,
            ping_url="https://api.anthropic.com/v1/models" if anthropic_base == "https://api.anthropic.com" else None,
        ))

    return ProviderRouter(providers, strategy)
