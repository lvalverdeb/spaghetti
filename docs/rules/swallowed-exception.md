# `swallowed-exception`

except block that silently discards the exception.

| Default severity | Tier | Default |
|---|---|---|
| warning | ast | on |

## Why it matters

An `except` that only `pass`es, logs nothing or returns a default hides failures until they surface somewhere unrelated.

## How to fix it

Catch the narrowest type, handle it (log with context, substitute a value *and* say why) or re-raise. A comment or logging call inside the block satisfies the rule.

## Precision

Strict **0%**, lenient **7%** over 30 sampled findings on the audit corpus ([method](../precision.md#method)).

## Elsewhere

- Ruff S110 (try-except-pass)

## Suppress a confirmed non-issue

`# spaghetti-ignore[swallowed-exception]: reason` on the flagged line or the line above.
