# `syntax-error`

File failed to parse.

| Default severity | Tier | Default |
|---|---|---|
| error | infra | on |

## Why it matters

A file that does not parse cannot be analysed, imported or run.

## How to fix it

Fix the syntax; if the file targets a newer Python than the scanner runs on, run spaghetti on that interpreter.

## Precision

Not in the Phase 1 sample (no findings on the corpus, or added later); see [docs/precision.md](../precision.md) for the Phase 2 addendum.

## Elsewhere

- Pylint E0001 syntax-error

## Suppress a confirmed non-issue

`# spaghetti-ignore[syntax-error]: reason` on the flagged line or the line above.
