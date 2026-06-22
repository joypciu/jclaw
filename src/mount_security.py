"""Mount security validation for additional container mounts."""
from __future__ import annotations

import json
import os
from dataclasses import asdict
from pathlib import Path
from typing import Optional

from .config import MOUNT_ALLOWLIST_PATH
from .logger import logger
from .schema import AdditionalMount, AllowedRoot, MountAllowlist


_cached_allowlist: MountAllowlist | None = None
_allowlist_load_error: str | None = None

_DEFAULT_BLOCKED_PATTERNS = [
    ".ssh",
    ".gnupg",
    ".gpg",
    ".aws",
    ".azure",
    ".gcloud",
    ".kube",
    ".docker",
    "credentials",
    ".env",
    ".netrc",
    ".npmrc",
    ".pypirc",
    "id_rsa",
    "id_ed25519",
    "private_key",
    ".secret",
]


def _expand_path(p: str) -> Path:
    return Path(os.path.expanduser(p)).resolve()


def _real_path(p: Path) -> Path | None:
    try:
        return p.resolve(strict=True)
    except Exception:
        return None


def _matches_blocked_pattern(real_path: Path, blocked_patterns: list[str]) -> str | None:
    parts = real_path.parts
    as_text = str(real_path)

    for pattern in blocked_patterns:
        for part in parts:
            if part == pattern or pattern in part:
                return pattern
        if pattern in as_text:
            return pattern

    return None


def _find_allowed_root(real_path: Path, allowed_roots: list[AllowedRoot]) -> AllowedRoot | None:
    for root in allowed_roots:
        real_root = _real_path(_expand_path(root.path))
        if real_root is None:
            continue
        try:
            real_path.relative_to(real_root)
            return root
        except ValueError:
            continue
    return None


def _is_valid_container_path(container_path: str) -> bool:
    if not container_path or not container_path.strip():
        return False
    if ".." in container_path:
        return False
    if container_path.startswith("/"):
        return False
    return True


def load_mount_allowlist() -> MountAllowlist | None:
    global _cached_allowlist, _allowlist_load_error

    if _cached_allowlist is not None:
        return _cached_allowlist

    if _allowlist_load_error is not None:
        return None

    try:
        path = Path(MOUNT_ALLOWLIST_PATH)
        if not path.exists():
            _allowlist_load_error = f"Mount allowlist not found at {path}"
            logger.warning(
                "Mount allowlist not found at %s; additional mounts are blocked",
                path,
            )
            return None

        parsed = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(parsed, dict):
            raise ValueError("allowlist root must be an object")

        allowed_roots_raw = parsed.get("allowedRoots")
        blocked_patterns_raw = parsed.get("blockedPatterns")
        non_main_read_only = parsed.get("nonMainReadOnly")

        if not isinstance(allowed_roots_raw, list):
            raise ValueError("allowedRoots must be an array")
        if not isinstance(blocked_patterns_raw, list):
            raise ValueError("blockedPatterns must be an array")
        if not isinstance(non_main_read_only, bool):
            raise ValueError("nonMainReadOnly must be a boolean")

        allowed_roots: list[AllowedRoot] = []
        for item in allowed_roots_raw:
            if not isinstance(item, dict):
                continue
            p = item.get("path")
            if not isinstance(p, str) or not p.strip():
                continue
            allowed_roots.append(
                AllowedRoot(
                    path=p,
                    allow_read_write=bool(item.get("allowReadWrite", False)),
                    description=item.get("description") if isinstance(item.get("description"), str) else None,
                )
            )

        blocked_patterns = sorted(set(_DEFAULT_BLOCKED_PATTERNS + [
            p for p in blocked_patterns_raw if isinstance(p, str)
        ]))

        _cached_allowlist = MountAllowlist(
            allowed_roots=allowed_roots,
            blocked_patterns=blocked_patterns,
            non_main_read_only=non_main_read_only,
        )

        logger.info(
            "Mount allowlist loaded (%s roots, %s blocked patterns)",
            len(_cached_allowlist.allowed_roots),
            len(_cached_allowlist.blocked_patterns),
        )

        return _cached_allowlist

    except Exception as exc:
        _allowlist_load_error = str(exc)
        logger.error(
            "Failed to load mount allowlist at %s: %s",
            MOUNT_ALLOWLIST_PATH,
            _allowlist_load_error,
        )
        return None


def validate_mount(mount: AdditionalMount, is_main: bool) -> dict[str, object]:
    allowlist = load_mount_allowlist()
    if allowlist is None:
        return {
            "allowed": False,
            "reason": f"No mount allowlist configured at {MOUNT_ALLOWLIST_PATH}",
        }

    container_path = mount.container_path or Path(mount.host_path).name
    if not _is_valid_container_path(container_path):
        return {
            "allowed": False,
            "reason": f"Invalid container path: {container_path}",
        }

    expanded = _expand_path(mount.host_path)
    real = _real_path(expanded)
    if real is None:
        return {
            "allowed": False,
            "reason": f"Host path does not exist: {mount.host_path} (expanded: {expanded})",
        }

    blocked = _matches_blocked_pattern(real, allowlist.blocked_patterns)
    if blocked is not None:
        return {
            "allowed": False,
            "reason": f"Path matches blocked pattern {blocked}: {real}",
        }

    allowed_root = _find_allowed_root(real, allowlist.allowed_roots)
    if allowed_root is None:
        roots = ", ".join(str(_expand_path(r.path)) for r in allowlist.allowed_roots)
        return {
            "allowed": False,
            "reason": f"Path {real} is not under any allowed root. Allowed roots: {roots}",
        }

    requested_rw = mount.readonly is False
    effective_readonly = True
    if requested_rw:
        if (not is_main) and allowlist.non_main_read_only:
            effective_readonly = True
        elif not allowed_root.allow_read_write:
            effective_readonly = True
        else:
            effective_readonly = False

    reason = f"Allowed under root {allowed_root.path}"
    if allowed_root.description:
        reason += f" ({allowed_root.description})"

    return {
        "allowed": True,
        "reason": reason,
        "real_host_path": str(real),
        "resolved_container_path": container_path,
        "effective_readonly": effective_readonly,
    }


def validate_additional_mounts(
    mounts: list[AdditionalMount],
    group_name: str,
    is_main: bool,
) -> list[dict[str, object]]:
    validated: list[dict[str, object]] = []

    for mount in mounts:
        result = validate_mount(mount, is_main)
        if bool(result.get("allowed")):
            validated.append(
                {
                    "hostPath": result["real_host_path"],
                    "containerPath": f"/workspace/extra/{result['resolved_container_path']}",
                    "readonly": result["effective_readonly"],
                }
            )
            logger.debug(
                "Mount validated for group %s: host=%s container=%s readonly=%s",
                group_name,
                result.get("real_host_path"),
                result.get("resolved_container_path"),
                result.get("effective_readonly"),
            )
        else:
            logger.warning(
                "Additional mount rejected for group %s: requested=%s container=%s reason=%s",
                group_name,
                mount.host_path,
                mount.container_path,
                result.get("reason"),
            )

    return validated


def generate_allowlist_template() -> str:
    template = {
        "allowedRoots": [
            {
                "path": "~/projects",
                "allowReadWrite": True,
                "description": "Development projects",
            },
            {
                "path": "~/repos",
                "allowReadWrite": True,
                "description": "Git repositories",
            },
            {
                "path": "~/Documents/work",
                "allowReadWrite": False,
                "description": "Work documents (read-only)",
            },
        ],
        "blockedPatterns": ["password", "secret", "token"],
        "nonMainReadOnly": True,
    }
    return json.dumps(template, indent=2)
