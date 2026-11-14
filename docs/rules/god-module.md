# `god-module`

Module exposes too many public symbols.

| Default severity | Tier | Default |
|---|---|---|
| warning | ast | on |

## Why it matters

A module exporting too many public names has no theme; importers take the whole thing and the import graph fills with hubs.

## How to fix it

Split into cohesive modules and give the package `__init__` an explicit `__all__` of what is genuinely public.

## Thresholds

| Key | Default |
|---|---|
| `max-public-symbols` | 15 |

## Precision

Strict **0%**, lenient **42%** over 12 sampled findings on the audit corpus ([method](../precision.md#method)).

## Elsewhere

No equivalent in Pylint, Ruff or SonarQube; spaghetti-specific.

## Suppress a confirmed non-issue

`# spaghetti-ignore[god-module]: reason` on the flagged line or the line above.
