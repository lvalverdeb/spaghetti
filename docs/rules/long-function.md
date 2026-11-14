# `long-function`

Function exceeds the line limit.

| Default severity | Tier | Default |
|---|---|---|
| warning | ast | on |

## Why it matters

A function that does not fit on a screen is hard to name, test and review; its length is usually several responsibilities sharing one scope.

## How to fix it

Extract Method: name each block that has its own comment or blank-line separation and move it out, passing the few values it needs. Flat, sequential pipelines are tolerated up to a low nesting depth.

## Thresholds

| Key | Default |
|---|---|
| `max-function-lines` | 50 |
| `long-function-flat-nesting-max` | 2 |

## Precision

Strict **36%**, lenient **67%** over 39 sampled findings on the audit corpus ([method](../precision.md#method)).

## Elsewhere

- Pylint R0915 too-many-statements
- Ruff PLR0915
- SonarQube python:S138

## Suppress a confirmed non-issue

`# spaghetti-ignore[long-function]: reason` on the flagged line or the line above.
