"""Runtime profile presets for quick provider/model setup."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Profile:
    name: str
    description: str
    env: dict[str, str]


PROFILES: dict[str, Profile] = {
    "anthropic-default": Profile(
        name="anthropic-default",
        description="Use Anthropic API with default model routing",
        env={},
    ),
    "openai-compatible": Profile(
        name="openai-compatible",
        description="Route model traffic through OpenAI-compatible endpoint",
        env={
            "JCLAW_ROUTER_STRATEGY": "balanced",
        },
    ),
    "local-ollama": Profile(
        name="local-ollama",
        description="Prefer local Ollama provider with cost-first routing",
        env={
            "JCLAW_ROUTER_STRATEGY": "cost",
            "JCLAW_LOCAL_COST": "0.0",
        },
    ),
    "local-fast": Profile(
        name="local-fast",
        description="Prefer lowest-latency provider",
        env={
            "JCLAW_ROUTER_STRATEGY": "latency",
        },
    ),
}


def list_profiles() -> list[Profile]:
    return [PROFILES[k] for k in sorted(PROFILES.keys())]


def get_profile(name: str) -> Profile | None:
    return PROFILES.get(name)
