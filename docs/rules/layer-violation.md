# `layer-violation`

Import forbidden by the configured layer rules.

| Default severity | Tier | Default |
|---|---|---|
| error | ast | on |

## Why it matters

An import across a configured layer boundary (`[tool.spaghetti.layers]`) means the architecture on the whiteboard and the one in the code differ.

## How to fix it

Depend on an abstraction the lower layer implements and inject it, or move the code to the layer it belongs to.

## Thresholds

| Key | Default |
|---|---|
| `max-cross-layer-imports` | 0 |

## Precision

Not in the Phase 1 sample (no findings on the corpus, or added later); see [docs/precision.md](../precision.md) for the Phase 2 addendum.

## Elsewhere

- import-linter forbidden contract
- Tach

## Suppress a confirmed non-issue

`# spaghetti-ignore[layer-violation]: reason` on the flagged line or the line above.
