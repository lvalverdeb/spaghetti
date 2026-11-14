# `star-import`

from x import *.

| Default severity | Tier | Default |
|---|---|---|
| warning | ast | **off** (enable with `[tool.spaghetti.rules]`) |

## Why it matters

`from x import *` makes the namespace depend on another module's contents and defeats static analysis.

## How to fix it

Import the names you use. Off by default: Ruff F403 owns this.

## Precision

Strict **0%**, lenient **0%** over 12 sampled findings on the audit corpus ([method](../precision.md#method)).

## Elsewhere

- Ruff F403
- Pylint W0401 wildcard-import

## Suppress a confirmed non-issue

`# spaghetti-ignore[star-import]: reason` on the flagged line or the line above.
