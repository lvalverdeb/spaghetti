# `scan-limit`

File skipped: over the size or nesting limit.

| Default severity | Tier | Default |
|---|---|---|
| info | infra | on |

## Why it matters

A file over the size limit or nested past the interpreter's recursion limit is almost always generated code or data; analysing it would cost minutes and say nothing about the hand-written code around it.

## How to fix it

Exclude generated files with `exclude`, or raise `max-file-bytes` if the file is genuinely hand-written. Nothing in it was checked.

## Thresholds

| Key | Default |
|---|---|
| `max-file-bytes` | 1048576 |

## Precision

Not sampled (no findings on the audit corpus); see [docs/precision.md](../precision.md).

## Elsewhere

No equivalent in Pylint, Ruff or SonarQube; spaghetti-specific.

## Suppress a confirmed non-issue

`# spaghetti-ignore[scan-limit]: reason` on the flagged line or the line above.
