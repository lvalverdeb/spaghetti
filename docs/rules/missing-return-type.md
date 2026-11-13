# `missing-return-type`

Public function without a return type annotation.

| Default severity | Tier | Default |
|---|---|---|
| warning | ast | on |

## Why it matters

Without a return annotation callers guess, type checkers give up at the boundary, and the contract lives only in the docstring.

## How to fix it

Annotate the return type; use `-> None` for procedures. Private (underscore) functions and tests are exempt.

## Precision

Strict **43%**, lenient **71%** over 7 sampled findings on httpx, rich, fastapi, pydantic and black ([method](../precision.md#method)).

## Elsewhere

- Ruff ANN201
- mypy --disallow-untyped-defs

## Suppress a confirmed non-issue

`# spaghetti-ignore[missing-return-type]: reason` on the flagged line or the line above.
