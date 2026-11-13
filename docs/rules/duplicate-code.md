# `duplicate-code`

Duplicated token sequence (type-2 clone) across files.

| Default severity | Tier | Default |
|---|---|---|
| info | workspace | on |

## Why it matters

Type-2 clones (same structure, renamed identifiers and literals) are the duplication tools like PMD-CPD and Sonar report; renaming variables does not make a copy independent.

## How to fix it

Extract the shared logic into one function parameterised by what differs; for test setup prefer fixtures or parametrisation over copied blocks.

## Thresholds

| Key | Default |
|---|---|
| `min-clone-tokens` | 50 |

## Precision

Not in the Phase 1 sample (no findings on the corpus, or added later); see [docs/precision.md](../precision.md) for the Phase 2 addendum.

## Elsewhere

- SonarQube duplicated blocks (CPD)
- PMD CPD
- jscpd

## Suppress a confirmed non-issue

`# spaghetti-ignore[duplicate-code]: reason` on the flagged line or the line above.
