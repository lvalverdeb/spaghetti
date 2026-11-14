# `sync-async-duplication`

Sync/async twin functions with near-identical bodies.

| Default severity | Tier | Default |
|---|---|---|
| warning | package | on |

## Why it matters

A sync/async twin pair whose bodies are near-identical doubles every bug fix, and the two drift apart silently.

## How to fix it

Push the shared logic into pure helpers both variants call, or generate one from the other (`unasync`), or keep only the async variant and wrap it for sync callers.

## Thresholds

| Key | Default |
|---|---|
| `default-twin-similarity` | 0.6 |
| `min-twin-function-lines` | 4 |

## Precision

Strict **0%**, lenient **0%** over 8 sampled findings on the audit corpus ([method](../precision.md#method)).

## Elsewhere

No equivalent in Pylint, Ruff or SonarQube; spaghetti-specific.

## Suppress a confirmed non-issue

`# spaghetti-ignore[sync-async-duplication]: reason` on the flagged line or the line above.
