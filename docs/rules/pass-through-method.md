# `pass-through-method`

Method whose body only delegates with unchanged arguments.

| Default severity | Tier | Default |
|---|---|---|
| info | ast | **off** (enable with `[tool.spaghetti.rules]`) |

## Why it matters

A method whose body only forwards its unchanged arguments to another object is a Middle Man: one more place to update for no added meaning.

## How to fix it

Let callers use the delegate directly, or give the method a reason to exist (validation, translation, a narrower interface). Intended Facades are the common false positive, which is why this rule ships off.

## Precision

Strict **0%**, lenient **0%** over 38 sampled findings on httpx, rich, fastapi, pydantic and black ([method](../precision.md#method)).

## Elsewhere

No equivalent in Pylint, Ruff or SonarQube; spaghetti-specific.

## Suppress a confirmed non-issue

`# spaghetti-ignore[pass-through-method]: reason` on the flagged line or the line above.
