# `low-maintainability`

Maintainability Index below threshold.

| Default severity | Tier | Default |
|---|---|---|
| warning | text | on |

## Why it matters

The Maintainability Index folds Halstead volume, cyclomatic complexity, lines and comment ratio into one 0–100 number per file; below the threshold a file is dense, branchy *and* long at once.

## How to fix it

Split the module by responsibility, then apply the complexity and length fixes to what remains. Adding comments alone raises MI only marginally.

## Thresholds

| Key | Default |
|---|---|
| `min-maintainability-index` | 10 |

## Precision

Not in the Phase 1 sample (no findings on the corpus, or added later); see [docs/precision.md](../precision.md) for the Phase 2 addendum.

## Elsewhere

- Radon mi
- Visual Studio Maintainability Index

## Suppress a confirmed non-issue

`# spaghetti-ignore[low-maintainability]: reason` on the flagged line or the line above.
