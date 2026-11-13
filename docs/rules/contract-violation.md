# `contract-violation`

Import forbidden by an architecture contract.

| Default severity | Tier | Default |
|---|---|---|
| error | workspace | on |

## Why it matters

An import that breaks an architecture contract (`[[tool.spaghetti.contracts]]`, forbidden edges or ordered layers) is the custom rule teams actually want: the design decision, enforced.

## How to fix it

Apply the Dependency Inversion Principle: depend on a `Protocol` the lower layer implements and inject it; or add the edge to `allow` if it is a deliberate exception.

## Precision

Not in the Phase 1 sample (no findings on the corpus, or added later); see [docs/precision.md](../precision.md) for the Phase 2 addendum.

## Elsewhere

- import-linter contracts
- Tach
- ArchUnit (Java)

## Suppress a confirmed non-issue

`# spaghetti-ignore[contract-violation]: reason` on the flagged line or the line above.
