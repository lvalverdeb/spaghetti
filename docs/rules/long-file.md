# `long-file`

File exceeds the line limit.

| Default severity | Tier | Default |
|---|---|---|
| warning | text | on |

## Why it matters

A file past a few hundred lines is a module with several reasons to change; merge conflicts and import-time cost follow.

## How to fix it

Split by cohesion: group the functions that share private helpers or state into their own module and re-export from the package `__init__` if the public path must stay.

## Thresholds

| Key | Default |
|---|---|
| `max-file-lines` | 400 |

## Precision

Strict **23%**, lenient **62%** over 39 sampled findings on httpx, rich, fastapi, pydantic and black ([method](../precision.md#method)).

## Elsewhere

- Pylint C0302 too-many-lines
- SonarQube python:S104

## Suppress a confirmed non-issue

`# spaghetti-ignore[long-file]: reason` on the flagged line or the line above.
