# `excessive-returns`

Function with too many return statements.

| Default severity | Tier | Default |
|---|---|---|
| info | ast | on |

## Why it matters

Many return points mean many ways the function can finish; the combination with branching makes the result type and invariants hard to state.

## How to fix it

Collapse a branch ladder that returns different values into a dictionary lookup or a single result variable; keep early returns only for guard clauses.

## Thresholds

| Key | Default |
|---|---|
| `max-returns` | 3 |

## Precision

Strict **10%**, lenient **55%** over 20 sampled findings on httpx, rich, fastapi, pydantic and black ([method](../precision.md#method)).

## Elsewhere

- Pylint R0911 too-many-return-statements
- Ruff PLR0911
- SonarQube python:S1142

## Suppress a confirmed non-issue

`# spaghetti-ignore[excessive-returns]: reason` on the flagged line or the line above.
