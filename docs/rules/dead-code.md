# `dead-code`

Statement unreachable after return/raise/break/continue.

| Default severity | Tier | Default |
|---|---|---|
| warning | ast | on |

## Why it matters

Statements after `return`/`raise`/`break`/`continue` never run; they are misleading at best and a sign of a botched edit at worst.

## How to fix it

Delete them, or move them before the exit if they were meant to run.

## Precision

Strict **0%**, lenient **0%** over 3 sampled findings on the audit corpus ([method](../precision.md#method)).

## Elsewhere

- Pylint W0101 unreachable
- SonarQube python:S1763

## Suppress a confirmed non-issue

`# spaghetti-ignore[dead-code]: reason` on the flagged line or the line above.
