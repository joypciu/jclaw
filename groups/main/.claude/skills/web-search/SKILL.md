---
name: web-search
description: Structured web research workflow using WebSearch, WebFetch, and browser tools. Use when the user asks for up-to-date information, comparisons, or external facts.
---

# /web-search

Use this skill for fast, high-quality research with traceable sources.

## When to use

- Current events, prices, release notes, docs updates
- Comparisons across tools, APIs, vendors, or models
- Any request where hallucination risk is high

## Workflow

1. Clarify goal in one sentence.
2. Run 3 to 5 WebSearch queries with varied phrasing.
3. Open top results with WebFetch and keep only high-signal sources.
4. If WebFetch content is incomplete or JS-rendered, use /agent-browser or /host-browser to fetch the live page state.
5. Cross-check claims in at least 2 independent sources.
6. Return a concise answer with sources and confidence.

## Query patterns

- Baseline: <topic> <year>
- Primary source: <topic> official docs
- Change log: <product> release notes
- Contradiction check: <claim> fact check

## Source quality order

1. Official docs and primary announcements
2. Maintainer repos and issue trackers
3. Reputable technical publications
4. Forums and social posts (supporting evidence only)

## Output format

- Answer: direct response in plain language
- Key facts: 3 to 7 bullets
- Sources: bullet list of title plus URL
- Confidence: high, medium, or low with one short reason

## Notes

- Prefer recency for rapidly changing topics.
- For coding questions, prioritize official API docs and source repositories.
- If sources conflict, state the conflict explicitly instead of guessing.
- If search tooling is unavailable, clearly state the limitation and switch to direct URL fetch + browser extraction.
