# `deep-inheritance`

Inheritance chain deeper than the limit.

| Default severity | Tier | Default |
|---|---|---|
| warning | ast | on |

## Why it matters

Behaviour inherited through four or more levels can only be understood by reading every ancestor; overrides interact in ways nobody designed.

## How to fix it

Flatten: compose the behaviours as collaborators or mixins with one job each, and keep hierarchies to a base plus concrete leaves.

## Thresholds

| Key | Default |
|---|---|
| `max-inheritance-depth` | 4 |

## Precision

Strict **0%**, lenient **18%** over 11 sampled findings on httpx, rich, fastapi, pydantic and black ([method](../precision.md#method)).

## Elsewhere

- Pylint R0901 too-many-ancestors

## Suppress a confirmed non-issue

`# spaghetti-ignore[deep-inheritance]: reason` on the flagged line or the line above.
