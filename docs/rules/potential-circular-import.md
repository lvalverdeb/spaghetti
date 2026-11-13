# `potential-circular-import`

Child module imports its parent package.

| Default severity | Tier | Default |
|---|---|---|
| warning | ast | **off** (enable with `[tool.spaghetti.rules]`) |

## Why it matters

A child module importing its parent package often creates a cycle through the package `__init__`.

## How to fix it

Import the sibling module directly. Off by default: `import-cycle` detects the real cycles on the workspace graph.

## Precision

Strict **0%**, lenient **4%** over 26 sampled findings on httpx, rich, fastapi, pydantic and black ([method](../precision.md#method)).

## Elsewhere

- Pylint R0401 cyclic-import (partial)

## Suppress a confirmed non-issue

`# spaghetti-ignore[potential-circular-import]: reason` on the flagged line or the line above.
