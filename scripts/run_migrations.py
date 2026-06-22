#!/usr/bin/env python3
"""Run versioned project migrations between two semantic versions.

Usage:
  python scripts/run_migrations.py <from-version> <to-version> <new-core-path>
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path


def compare_semver(a: str, b: str) -> int:
    pa = [int(x) for x in a.split(".")]
    pb = [int(x) for x in b.split(".")]
    size = max(len(pa), len(pb))
    pa += [0] * (size - len(pa))
    pb += [0] * (size - len(pb))
    for av, bv in zip(pa, pb):
        if av < bv:
            return -1
        if av > bv:
            return 1
    return 0


@dataclass
class MigrationResult:
    version: str
    success: bool
    error: str | None = None


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print(
            "Usage: python scripts/run_migrations.py <from-version> <to-version> <new-core-path>",
            file=sys.stderr,
        )
        return 1

    from_version, to_version, new_core_path = argv
    root = Path.cwd()
    migrations_dir = Path(new_core_path) / "migrations"

    if not migrations_dir.exists():
        print(json.dumps({"migrationsRun": 0, "results": []}, indent=2))
        return 0

    pattern = re.compile(r"^\d+\.\d+\.\d+$")
    versions = sorted(
        [p.name for p in migrations_dir.iterdir() if p.is_dir() and pattern.match(p.name)],
        key=lambda v: [int(x) for x in v.split(".")],
    )
    target_versions = [
        v for v in versions if compare_semver(v, from_version) > 0 and compare_semver(v, to_version) <= 0
    ]

    results: list[MigrationResult] = []

    for version in target_versions:
        py_entry = migrations_dir / version / "index.py"
        ts_entry = migrations_dir / version / "index.ts"

        if py_entry.exists():
            cmd = [sys.executable, str(py_entry), str(root)]
        elif ts_entry.exists():
            cmd = ["npx", "tsx", str(ts_entry), str(root)]
        else:
            results.append(MigrationResult(version=version, success=False, error=f"Migration {version}/index.py or index.ts not found"))
            continue

        try:
            subprocess.run(cmd, cwd=root, check=True, capture_output=True, text=True, timeout=120)
            results.append(MigrationResult(version=version, success=True))
        except Exception as exc:
            results.append(MigrationResult(version=version, success=False, error=str(exc)))

    payload = {
        "migrationsRun": len(results),
        "results": [r.__dict__ for r in results],
    }
    print(json.dumps(payload, indent=2))

    return 1 if any(not r.success for r in results) else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
