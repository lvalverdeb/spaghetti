# `boolean-flag-params`

Function with several boolean flag parameters.

| Default severity | Tier | Default |
|---|---|---|
| info | ast | on |

## Why it matters

Several boolean flags multiply the behaviours one function implements (2ⁿ) and read as `f(data, True, False, True)` at the call site.

## How to fix it

Split into one function per behaviour, or replace the flags with an `Enum` or a small options dataclass; at minimum make them keyword-only.

## Thresholds

| Key | Default |
|---|---|
| `min-boolean-flags` | 3 |

## Precision

Strict **3%**, lenient **12%** over 33 sampled findings on httpx, rich, fastapi, pydantic and black ([method](../precision.md#method)).

## Elsewhere

- Ruff FBT001/FBT002 (flake8-boolean-trap)

## Suppress a confirmed non-issue

`# spaghetti-ignore[boolean-flag-params]: reason` on the flagged line or the line above.
