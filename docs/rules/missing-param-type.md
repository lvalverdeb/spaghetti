# `missing-param-type`

Public function parameter without a type annotation.

| Default severity | Tier | Default |
|---|---|---|
| info | ast | on |

## Why it matters

An unannotated public parameter forces every caller to read the body to learn what to pass.

## How to fix it

Annotate the parameter; prefer `Protocol`s or ABCs over concrete classes when several types are accepted.

## Precision

Strict **0%**, lenient **86%** over 7 sampled findings on the audit corpus ([method](../precision.md#method)).

## Elsewhere

- Ruff ANN001

## Suppress a confirmed non-issue

`# spaghetti-ignore[missing-param-type]: reason` on the flagged line or the line above.
