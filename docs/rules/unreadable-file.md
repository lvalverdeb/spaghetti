# `unreadable-file`

File could not be read.

| Default severity | Tier | Default |
|---|---|---|
| error | infra | on |

## Why it matters

A file that cannot be read (permissions, invalid encoding) is a blind spot in every report.

## How to fix it

Fix permissions or re-encode as UTF-8; exclude generated or binary paths with `exclude`.

## Precision

Not in the Phase 1 sample (no findings on the corpus, or added later); see [docs/precision.md](../precision.md) for the Phase 2 addendum.

## Elsewhere

No equivalent in Pylint, Ruff or SonarQube; spaghetti-specific.

## Suppress a confirmed non-issue

`# spaghetti-ignore[unreadable-file]: reason` on the flagged line or the line above.
