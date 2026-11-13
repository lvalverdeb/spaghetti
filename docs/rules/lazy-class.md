# `lazy-class`

Class with fewer than two methods.

| Default severity | Tier | Default |
|---|---|---|
| info | ast | on |

## Why it matters

A class with fewer than two methods is usually a record with ceremony, or a function that wanted to be a class.

## How to fix it

Make it a `dataclass`/`NamedTuple` if it holds data, a plain function if it holds behaviour. Exceptions, Protocols, Enums and ABCs are exempt.

## Thresholds

| Key | Default |
|---|---|
| `min-class-methods` | 2 |

## Precision

Strict **0%**, lenient **0%** over 27 sampled findings on httpx, rich, fastapi, pydantic and black ([method](../precision.md#method)).

## Elsewhere

- Pylint R0903 too-few-public-methods

## Suppress a confirmed non-issue

`# spaghetti-ignore[lazy-class]: reason` on the flagged line or the line above.
