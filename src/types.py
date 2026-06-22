# Backward-compatibility shim — real definitions live in schema.py.
# Renamed to avoid shadowing Python's built-in `types` module which caused
# circular import errors: GenericAlias / SimpleNamespace couldn't be resolved.
from .schema import (  # noqa: F401  re-export everything
    AdditionalMount,
    AllowedRoot,
    Channel,
    ContainerConfig,
    MountAllowlist,
    NewMessage,
    OnChatMetadata,
    OnInboundMessage,
    RegisteredGroup,
    ScheduledTask,
    TaskRunLog,
)
