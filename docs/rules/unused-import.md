# `unused-import`

Imported name never referenced.

| Default severity | Tier | Default |
|---|---|---|
| warning | ast | **off** (enable with `[tool.spaghetti.rules]`) |

## Why it matters

An import nobody uses costs import time and misleads about dependencies.

## How to fix it

Remove it. Off by default: Ruff F401 owns this and does it better.

## Precision

Strict **0%**, lenient **0%** over 33 sampled findings on httpx, rich, fastapi, pydantic and black ([method](../precision.md#method)).

## Elsewhere

- Ruff F401
- Pylint W0611

## Suppress a confirmed non-issue

`# spaghetti-ignore[unused-import]: reason` on the flagged line or the line above.
