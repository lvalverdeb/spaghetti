# `magic-number`

Unexplained numeric literal.

| Default severity | Tier | Default |
|---|---|---|
| info | ast | **off** (enable with `[tool.spaghetti.rules]`) |

## Why it matters

An unexplained literal carries a meaning the reader must reverse-engineer and is duplicated wherever the meaning is reused.

## How to fix it

Name it as a module-level constant (or an `Enum` member) where the meaning lives. Off by default: index arithmetic and obvious constants dominate real hits.

## Precision

Strict **2%**, lenient **18%** over 40 sampled findings on the audit corpus ([method](../precision.md#method)).

## Elsewhere

- Ruff PLR2004 magic-value-comparison
- SonarQube python:S109

## Suppress a confirmed non-issue

`# spaghetti-ignore[magic-number]: reason` on the flagged line or the line above.
