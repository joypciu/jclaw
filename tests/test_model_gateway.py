"""Tests for src/model_gateway.py — ProviderRouter health-aware routing."""
from __future__ import annotations

import asyncio
import time
from unittest.mock import AsyncMock, patch

import pytest

from src.model_gateway import ProviderConfig, ProviderRouter, _Health


def _make_router(n: int = 3, strategy: str = "balanced") -> ProviderRouter:
    providers = [
        ProviderConfig(name=f"p{i}", base_url=f"http://p{i}.local", cost_multiplier=float(i + 1))
        for i in range(n)
    ]
    return ProviderRouter(providers, strategy=strategy)  # type: ignore[arg-type]


def test_select_provider_returns_healthy():
    router = _make_router(3)
    # All healthy by default — should return the lowest-score one
    p = router.select_provider()
    assert p.name in {"p0", "p1", "p2"}


def test_unhealthy_provider_excluded():
    router = _make_router(3)
    # Mark p0 and p1 as unhealthy
    router._health["p0"].unhealthy_until = time.monotonic() + 60
    router._health["p1"].unhealthy_until = time.monotonic() + 60
    p = router.select_provider()
    assert p.name == "p2"


def test_fallback_when_all_unhealthy():
    router = _make_router(2)
    for name in ["p0", "p1"]:
        router._health[name].unhealthy_until = time.monotonic() + 60
    # Should still return *something* (best of the unhealthy set)
    p = router.select_provider()
    assert p is not None


def test_record_success_updates_ema():
    router = _make_router(1)
    initial = router._health["p0"].ema_latency_ms
    router.record_success("p0", latency_ms=10.0)
    assert router._health["p0"].ema_latency_ms < initial  # latency improved


def test_record_error_sets_backoff():
    router = _make_router(1)
    router.record_error("p0")
    assert router._health["p0"].unhealthy_until > time.monotonic()
    assert router._health["p0"].error_count == 1


def test_strategy_latency_picks_fastest():
    router = _make_router(3, strategy="latency")
    router._health["p0"].ema_latency_ms = 500.0
    router._health["p1"].ema_latency_ms = 50.0
    router._health["p2"].ema_latency_ms = 300.0
    p = router.select_provider()
    assert p.name == "p1"


def test_strategy_cost_picks_cheapest():
    router = _make_router(3, strategy="cost")
    # cost_multiplier: p0=1.0, p1=2.0, p2=3.0
    p = router.select_provider()
    assert p.name == "p0"


def test_health_snapshot_structure():
    router = _make_router(2)
    snap = router.health_snapshot()
    assert len(snap) == 2
    for entry in snap:
        assert "provider" in entry
        assert "healthy" in entry
        assert "ema_latency_ms" in entry
        assert "error_count" in entry


def test_initialize_pings_all_providers_sync():
    # Synchronous wrapper to avoid pytest-asyncio dependency issues
    router = _make_router(2)
    async def _init():
        with patch.object(router, "_ping", new_callable=AsyncMock) as mock_ping:
            await router.initialize()
            assert mock_ping.call_count == 2
            assert router._initialized is True
    asyncio.run(_init())
