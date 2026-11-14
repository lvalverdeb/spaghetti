# `too-many-params`

Function with too many parameters.

| Default severity | Tier | Default |
|---|---|---|
| warning | ast | on |

## Why it matters

Long parameter lists are hard to call correctly and usually mean several values that travel together have no name of their own.

## How to fix it

Introduce Parameter Object: bundle the related arguments into a dataclass (or pass the object they came from); make rarely used ones keyword-only with defaults. `self`, `cls`, `*args` and `**kwargs` do not count.

## Thresholds

| Key | Default |
|---|---|
| `max-func-params` | 6 |

## Precision

Strict **2%**, lenient **28%** over 40 sampled findings on the audit corpus ([method](../precision.md#method)).

## Elsewhere

- Pylint R0913 too-many-arguments
- Ruff PLR0913
- SonarQube python:S107

## Suppress a confirmed non-issue

`# spaghetti-ignore[too-many-params]: reason` on the flagged line or the line above.
