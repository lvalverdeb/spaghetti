# `high-coupling`

Module with both high fan-in and high fan-out.

| Default severity | Tier | Default |
|---|---|---|
| warning | workspace | on |

## Why it matters

A module with both high fan-in and high fan-out is a hub: every change in it ripples out, and every change around it ripples in.

## How to fix it

Split the module so that the widely imported part (stable types, constants) is separate from the part that imports many others (orchestration).

## Thresholds

| Key | Default |
|---|---|
| `max-module-fan-in` | 8 |
| `max-module-fan-out` | 8 |

## Precision

Strict **0%**, lenient **50%** over 4 sampled findings on the audit corpus ([method](../precision.md#method)).

## Elsewhere

- CodeScene / SonarQube afferent–efferent coupling

## Suppress a confirmed non-issue

`# spaghetti-ignore[high-coupling]: reason` on the flagged line or the line above.
