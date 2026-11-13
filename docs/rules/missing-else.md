# `missing-else`

Multi-statement if without else.

| Default severity | Tier | Default |
|---|---|---|
| info | ast | **off** (enable with `[tool.spaghetti.rules]`) |

## Why it matters

A multi-statement `if` without `else` leaves the negative path implicit; the reader cannot tell if it was considered.

## How to fix it

Add the `else` (or a comment stating the fall-through is intended). Off by default: the audit found no true positives.

## Precision

Strict **0%**, lenient **0%** over 40 sampled findings on httpx, rich, fastapi, pydantic and black ([method](../precision.md#method)).

## Elsewhere

- SonarQube python:S126

## Suppress a confirmed non-issue

`# spaghetti-ignore[missing-else]: reason` on the flagged line or the line above.
