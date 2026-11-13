# `high-cognitive-complexity`

Cognitive complexity (Sonar) above threshold.

| Default severity | Tier | Default |
|---|---|---|
| warning | ast | on |

## Why it matters

Sonar's cognitive complexity scores what makes code hard to *read* — nesting, breaks in linear flow, mixed boolean operators — rather than paths. It is the metric most correlated with reviewer effort.

## How to fix it

Flatten nesting with early returns and extracted helpers; one condition per `if`; replace nested loops over the same data with a single comprehension or a generator pipeline.

## Thresholds

| Key | Default |
|---|---|
| `cognitive-complexity-threshold` | 15 |

## Precision

Not in the Phase 1 sample (no findings on the corpus, or added later); see [docs/precision.md](../precision.md) for the Phase 2 addendum.

## Elsewhere

- SonarQube python:S3776

## Suppress a confirmed non-issue

`# spaghetti-ignore[high-cognitive-complexity]: reason` on the flagged line or the line above.
