# `import-cycle`

Circular import between modules.

| Default severity | Tier | Default |
|---|---|---|
| error | workspace | on |

## Why it matters

Modules that import each other can only be loaded in one order, break on `from x import name`, and cannot be tested or reused apart.

## How to fix it

Move the shared types to a leaf module both import; invert the dependency with a `Protocol`; or make one import lazy inside the function that needs it.

## Precision

Strict **0%**, lenient **0%** over 16 sampled findings on httpx, rich, fastapi, pydantic and black ([method](../precision.md#method)).

## Elsewhere

- Pylint R0401 cyclic-import
- import-linter

## Suppress a confirmed non-issue

`# spaghetti-ignore[import-cycle]: reason` on the flagged line or the line above.
