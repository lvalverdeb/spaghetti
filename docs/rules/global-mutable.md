# `global-mutable`

Mutable state assigned at module scope.

| Default severity | Tier | Default |
|---|---|---|
| info | ast | on |

## Why it matters

A mutable object at module scope is shared state across every importer and every test; it makes order-dependent bugs possible.

## How to fix it

Make it immutable (tuple, frozenset, `Final` constant), or move it into an object that is created explicitly and passed where needed.

## Precision

Strict **0%**, lenient **0%** over 24 sampled findings on the audit corpus ([method](../precision.md#method)).

## Elsewhere

No equivalent in Pylint, Ruff or SonarQube; spaghetti-specific.

## Suppress a confirmed non-issue

`# spaghetti-ignore[global-mutable]: reason` on the flagged line or the line above.
