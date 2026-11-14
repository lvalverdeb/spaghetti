# `duplicate-code`

Duplicated token sequence (type-2 clone) across files.

| Default severity | Tier | Default |
|---|---|---|
| info | workspace | on |

## Why it matters

Type-2 clones (same structure, renamed identifiers and literals) are the duplication tools like PMD-CPD and Sonar report; renaming variables does not make a copy independent. Only clones containing logic count: import blocks, declaration tables and argument forwarding are not reported, and a clone never runs from one function or class into a differently named neighbour.

## How to fix it

Extract the shared logic into one function parameterised by what differs; for test setup prefer fixtures or parametrisation over copied blocks.

## Thresholds

| Key | Default |
|---|---|
| `min-clone-tokens` | 50 |

## Precision

Strict **30%**, lenient **57%** over 30 sampled findings on the audit corpus ([method](../precision.md#method)).

## Elsewhere

- SonarQube duplicated blocks (CPD)
- PMD CPD
- jscpd

## Suppress a confirmed non-issue

`# spaghetti-ignore[duplicate-code]: reason` on the flagged line or the line above.
