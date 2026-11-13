# `god-class`

Class with too many methods, attributes, or total complexity.

| Default severity | Tier | Default |
|---|---|---|
| warning | ast | on |

## Why it matters

A class with too many methods, attributes or total complexity is a module in disguise; everything depends on it and nobody dares change it.

## How to fix it

Extract Class along the attribute clusters its methods actually use (the LCOM groups); delegate to the new collaborators instead of inheriting from them.

## Thresholds

| Key | Default |
|---|---|
| `max-class-methods` | 25 |
| `max-class-attrs` | 20 |
| `max-class-wmc` | 50 |

## Precision

Strict **28%**, lenient **81%** over 32 sampled findings on httpx, rich, fastapi, pydantic and black ([method](../precision.md#method)).

## Elsewhere

- Pylint R0902 too-many-instance-attributes
- Pylint R0904 too-many-public-methods
- Ruff PLR0904

## Suppress a confirmed non-issue

`# spaghetti-ignore[god-class]: reason` on the flagged line or the line above.
