# `deep-nesting`

Control flow nested too deeply.

| Default severity | Tier | Default |
|---|---|---|
| warning | ast | on |

## Why it matters

Every nesting level is a precondition the reader must carry; past four or five the indentation itself hides which branch a line belongs to.

## How to fix it

Invert conditions into guard clauses that `return`/`continue` early, extract the inner block into a named function, or replace nested loops with a comprehension.

## Thresholds

| Key | Default |
|---|---|
| `max-nesting-depth` | 5 |

## Precision

Strict **47%**, lenient **81%** over 32 sampled findings on the audit corpus ([method](../precision.md#method)).

## Elsewhere

- Pylint R1702 too-many-nested-blocks
- SonarQube python:S134

## Suppress a confirmed non-issue

`# spaghetti-ignore[deep-nesting]: reason` on the flagged line or the line above.
