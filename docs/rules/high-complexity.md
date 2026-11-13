# `high-complexity`

Cyclomatic complexity (McCabe, Radon-exact) above threshold.

| Default severity | Tier | Default |
|---|---|---|
| warning | ast | on |

## Why it matters

Cyclomatic complexity counts independent paths; each one is a test case you owe and a branch a reader must hold in mind. Computed exactly as Radon does.

## How to fix it

Replace branch ladders with a dispatch table or polymorphism, hoist guard clauses to the top and return early, and split the function along its decision clusters.

## Thresholds

| Key | Default |
|---|---|
| `complexity-threshold` | 10 |

## Precision

Strict **40%**, lenient **95%** over 40 sampled findings on httpx, rich, fastapi, pydantic and black ([method](../precision.md#method)).

## Elsewhere

- Ruff C901 (mccabe)
- Pylint R1260 too-complex
- SonarQube python:S1541
- Radon cc

## Suppress a confirmed non-issue

`# spaghetti-ignore[high-complexity]: reason` on the flagged line or the line above.
