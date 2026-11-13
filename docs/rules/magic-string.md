# `magic-string`

Same string literal compared for equality in several places.

| Default severity | Tier | Default |
|---|---|---|
| info | ast | **off** (enable with `[tool.spaghetti.rules]`) |

## Why it matters

The same string compared for equality in several places is a stringly-typed enum; a typo in one copy is a silent bug.

## How to fix it

Replace with an `Enum` or a named constant. Off by default: domain vocabulary (schema keywords, token strings) is the usual hit.

## Precision

Strict **0%**, lenient **5%** over 37 sampled findings on httpx, rich, fastapi, pydantic and black ([method](../precision.md#method)).

## Elsewhere

- SonarQube python:S1192

## Suppress a confirmed non-issue

`# spaghetti-ignore[magic-string]: reason` on the flagged line or the line above.
