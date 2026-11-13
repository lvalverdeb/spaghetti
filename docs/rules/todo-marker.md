# `todo-marker`

TODO/FIXME/XXX/HACK comment.

| Default severity | Tier | Default |
|---|---|---|
| info | text | on |

## Why it matters

TODO/FIXME/XXX/HACK markers are debt with no owner or date; they accumulate until nobody trusts them.

## How to fix it

Turn each into a tracked issue and reference it, or do it now. Keep the marker only with the ticket id.

## Precision

Strict **86%**, lenient **91%** over 22 sampled findings on httpx, rich, fastapi, pydantic and black ([method](../precision.md#method)).

## Elsewhere

- Ruff FIX001–FIX004 / TD
- Pylint W0511 fixme
- SonarQube python:S1135

## Suppress a confirmed non-issue

`# spaghetti-ignore[todo-marker]: reason` on the flagged line or the line above.
