# `duplicate-function-body`

Identical function bodies across files.

| Default severity | Tier | Default |
|---|---|---|
| warning | package | on |

## Why it matters

Two functions with byte-identical bodies across files will be fixed in one place and not the other.

## How to fix it

Keep one, import it from the other module; if the copies must diverge, make the difference a parameter.

## Thresholds

| Key | Default |
|---|---|
| `default-min-duplicate-lines` | 5 |

## Precision

Strict **4%**, lenient **43%** over 23 sampled findings on the audit corpus ([method](../precision.md#method)).

## Elsewhere

- SonarQube python:S4144

## Suppress a confirmed non-issue

`# spaghetti-ignore[duplicate-function-body]: reason` on the flagged line or the line above.
