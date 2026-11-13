# `transport-in-library`

Library package imports a transport framework.

| Default severity | Tier | Default |
|---|---|---|
| error | ast | on |

## Why it matters

A library package importing a web framework can no longer be used without that framework, and its tests need an HTTP stack.

## How to fix it

Keep transport (FastAPI, Flask, httpx…) in an adapter package; the library exposes plain functions and dataclasses the adapter calls.

## Precision

Not in the Phase 1 sample (no findings on the corpus, or added later); see [docs/precision.md](../precision.md) for the Phase 2 addendum.

## Elsewhere

- import-linter forbidden contract

## Suppress a confirmed non-issue

`# spaghetti-ignore[transport-in-library]: reason` on the flagged line or the line above.
