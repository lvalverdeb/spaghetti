# `bare-except`

except: without an exception type.

| Default severity | Tier | Default |
|---|---|---|
| warning | ast | on |

## Why it matters

`except:` also catches `KeyboardInterrupt`, `SystemExit` and `MemoryError`, turning a clean shutdown into a hang.

## How to fix it

Name the exception type you expect; use `except Exception:` only at a top-level boundary that logs and re-raises or exits.

## Precision

Strict **0%**, lenient **0%** over 1 sampled findings on httpx, rich, fastapi, pydantic and black ([method](../precision.md#method)).

## Elsewhere

- Ruff E722
- Pylint W0702 bare-except

## Suppress a confirmed non-issue

`# spaghetti-ignore[bare-except]: reason` on the flagged line or the line above.
