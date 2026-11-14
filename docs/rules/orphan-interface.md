# `orphan-interface`

Abstract class with exactly one implementation.

| Default severity | Tier | Default |
|---|---|---|
| info | package | on |

## Why it matters

An abstract class with exactly one implementation is speculative generality: an indirection paid for every day for a flexibility nobody uses.

## How to fix it

Collapse the ABC into its only implementation; reintroduce the abstraction when the second implementation actually arrives.

## Precision

Strict **0%**, lenient **50%** over 2 sampled findings on the audit corpus ([method](../precision.md#method)).

## Elsewhere

No equivalent in Pylint, Ruff or SonarQube; spaghetti-specific.

## Suppress a confirmed non-issue

`# spaghetti-ignore[orphan-interface]: reason` on the flagged line or the line above.
