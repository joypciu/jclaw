---
name: repo-radar
description: Research software repositories quickly: architecture, maturity, risks, and fit for purpose. Useful for tasks inspired by claw-code and openclaude style repos.
---

# /repo-radar

Use this skill to evaluate one or more repositories before implementation.

## Goals

- Understand what the repo is good at
- Extract reusable patterns
- Avoid copying brittle or irrelevant pieces

## Workflow

1. Identify target repos and project goal.
2. Gather overview docs first (README, docs, architecture files).
3. Inspect implementation hotspots (entrypoints, routing, runtime, container, skills).
4. Compare across repos on the same dimensions.
5. Recommend concrete adaptation steps for J Claw.

## Comparison dimensions

- Runtime model and process boundaries
- Tooling and extensibility model
- Error handling and observability
- Security posture (secrets, sandboxing, permissions)
- Migration risk and maintenance burden

## Output format

- Fit summary: best repo ideas to adopt now
- Pattern picks: 3 to 6 patterns with rationale
- Anti-patterns: what to avoid
- Implementation list: concrete file-level changes for J Claw

## Practical rule

Adapt patterns, do not clone architecture blindly. Preserve J Claw's isolation model, IPC contracts, and channel abstractions.
