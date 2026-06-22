"""Channel registry for runtime self-registration."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional, Protocol

from ..types import Channel, OnChatMetadata, OnInboundMessage, RegisteredGroup


@dataclass
class ChannelOpts:
    on_message: OnInboundMessage
    on_chat_metadata: OnChatMetadata
    registered_groups: Callable[[], dict[str, RegisteredGroup]]


class ChannelFactory(Protocol):
    def __call__(self, opts: ChannelOpts) -> Optional[Channel]: ...


_registry: dict[str, ChannelFactory] = {}


def register_channel(name: str, factory: ChannelFactory) -> None:
    _registry[name] = factory


def get_channel_factory(name: str) -> ChannelFactory | None:
    return _registry.get(name)


def get_registered_channel_names() -> list[str]:
    return list(_registry.keys())
