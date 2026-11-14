# `duplicate-branch`

if/else branches with identical bodies.

| Default severity | Tier | Default |
|---|---|---|
| warning | ast | on |

## Why it matters

Two branches with identical bodies mean the condition is irrelevant or one branch is a copy-paste bug waiting to diverge.

## How to fix it

Delete the condition if both branches are truly the same; otherwise fix the branch that should differ.

## Precision

Strict **0%**, lenient **100%** over 1 sampled findings on the audit corpus ([method](../precision.md#method)).

## Elsewhere

- SonarQube python:S1871

## Suppress a confirmed non-issue

`# spaghetti-ignore[duplicate-branch]: reason` on the flagged line or the line above.
