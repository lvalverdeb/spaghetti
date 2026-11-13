# `untyped-dict`

Bare dict in a type hint.

| Default severity | Tier | Default |
|---|---|---|
| info | ast | on |

## Why it matters

A bare `dict` in a signature says nothing about keys or values; it is a type hole that spreads to everything downstream.

## How to fix it

Write `dict[str, int]`, or better a `TypedDict` or dataclass when the keys are fixed.

## Precision

Strict **33%**, lenient **33%** over 3 sampled findings on httpx, rich, fastapi, pydantic and black ([method](../precision.md#method)).

## Elsewhere

- mypy --disallow-any-generics

## Suppress a confirmed non-issue

`# spaghetti-ignore[untyped-dict]: reason` on the flagged line or the line above.
