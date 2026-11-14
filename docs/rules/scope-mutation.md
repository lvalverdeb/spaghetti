# `scope-mutation`

Function mutates outer scope via global/nonlocal.

| Default severity | Tier | Default |
|---|---|---|
| info | ast | on |

## Why it matters

`global`/`nonlocal` writes mean a function's effect is not visible in its signature; callers cannot reason about it locally.

## How to fix it

Return the new value and let the caller assign it, or hold the state in an object the function receives.

## Precision

Strict **0%**, lenient **94%** over 16 sampled findings on the audit corpus ([method](../precision.md#method)).

## Elsewhere

- Pylint W0603 global-statement
- Ruff PLW0603

## Suppress a confirmed non-issue

`# spaghetti-ignore[scope-mutation]: reason` on the flagged line or the line above.
