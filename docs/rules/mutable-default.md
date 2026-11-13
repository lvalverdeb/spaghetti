# `mutable-default`

Mutable default argument.

| Default severity | Tier | Default |
|---|---|---|
| warning | ast | on |

## Why it matters

A mutable default (`def f(x=[])`) is created once and shared by every call; the second caller sees the first caller's data.

## How to fix it

Default to `None` and create the object inside the function, or use `dataclasses.field(default_factory=...)`.

## Precision

Strict **0%**, lenient **100%** over 1 sampled findings on httpx, rich, fastapi, pydantic and black ([method](../precision.md#method)).

## Elsewhere

- Ruff B006
- Pylint W0102 dangerous-default-value

## Suppress a confirmed non-issue

`# spaghetti-ignore[mutable-default]: reason` on the flagged line or the line above.
