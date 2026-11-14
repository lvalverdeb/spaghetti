# `message-chain`

Attribute or call chain deeper than the limit.

| Default severity | Tier | Default |
|---|---|---|
| info | ast | on |

## Why it matters

`a.b().c.d()` couples the caller to the shape of three objects it does not own; a change in any of them breaks it (Law of Demeter).

## How to fix it

Hide Delegate: ask the first object for what you need, or pass the final object in directly.

## Thresholds

| Key | Default |
|---|---|
| `max-message-chain-depth` | 3 |

## Precision

Strict **0%**, lenient **50%** over 8 sampled findings on the audit corpus ([method](../precision.md#method)).

## Elsewhere

No equivalent in Pylint, Ruff or SonarQube; spaghetti-specific.

## Suppress a confirmed non-issue

`# spaghetti-ignore[message-chain]: reason` on the flagged line or the line above.
