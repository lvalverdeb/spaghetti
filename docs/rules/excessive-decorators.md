# `excessive-decorators`

Too many stacked decorators.

| Default severity | Tier | Default |
|---|---|---|
| info | ast | on |

## Why it matters

Many stacked decorators compose behaviours in an order the reader must simulate, and hide the function's real signature.

## How to fix it

Combine them into one purpose-built decorator, or move cross-cutting concerns (caching, retry, auth) to the framework layer that owns them.

## Thresholds

| Key | Default |
|---|---|
| `max-decorators` | 3 |

## Precision

Strict **0%**, lenient **0%** over 2 sampled findings on the audit corpus ([method](../precision.md#method)).

## Elsewhere

No equivalent in Pylint, Ruff or SonarQube; spaghetti-specific.

## Suppress a confirmed non-issue

`# spaghetti-ignore[excessive-decorators]: reason` on the flagged line or the line above.
