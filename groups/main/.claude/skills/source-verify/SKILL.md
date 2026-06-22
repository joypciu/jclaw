---
name: source-verify
description: Verify disputed or uncertain claims by triangulating sources and showing exactly what is confirmed vs unconfirmed.
---

# /source-verify

Use this skill when accuracy matters more than speed.

## Trigger conditions

- User asks: "is this true", "verify", "fact-check", "confirm"
- Research result has conflicting sources
- The topic is high impact (security, legal, money, health, policy)

## Verification procedure

1. Break the request into atomic claims.
2. Search each claim separately.
3. Find at least 2 independent high-quality sources per claim.
4. Mark each claim:
   - Confirmed
   - Partially confirmed
   - Unverified
   - Contradicted
5. Provide evidence links for every non-trivial claim.

## Evidence quality rubric

- Strong: official docs, standards, maintainers, public records
- Medium: established publications citing primary material
- Weak: unreferenced blog/forum/social statements

## Output template

- Verdict: one-line summary
- Claim table:
  - Claim
  - Status
  - Evidence
- Remaining uncertainty: what is still unknown
- Next step: best single follow-up action

## Guardrails

- Do not present unverified claims as facts.
- If evidence is weak, say so clearly.
- If no reliable sources exist, return "insufficient evidence".
