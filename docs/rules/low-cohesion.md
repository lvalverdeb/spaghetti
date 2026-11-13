# `low-cohesion`

Class whose methods split into unrelated groups (LCOM4).

| Default severity | Tier | Default |
|---|---|---|
| info | ast | **off** (enable with `[tool.spaghetti.rules]`) |

## Why it matters

LCOM4 counts the connected groups of methods that share attributes; more than one group means the class is several classes sharing a name.

## How to fix it

Split along the groups the message lists; each becomes its own class, or the stateless group becomes module-level functions.

## Thresholds

| Key | Default |
|---|---|
| `min-methods-for-cohesion` | 3 |
| `min-stateful-method-fraction` | 0.2 |

## Precision

Not in the Phase 1 sample (no findings on the corpus, or added later); see [docs/precision.md](../precision.md) for the Phase 2 addendum.

## Elsewhere

- CodeScene / SonarQube LCOM4 metric

## Suppress a confirmed non-issue

`# spaghetti-ignore[low-cohesion]: reason` on the flagged line or the line above.
