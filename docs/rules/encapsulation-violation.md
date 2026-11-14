# `encapsulation-violation`

Private attribute accessed from outside its owner.

| Default severity | Tier | Default |
|---|---|---|
| info | ast | on |

## Why it matters

Reaching into `obj._private` from another class couples the reader to an implementation detail the owner may change without notice.

## How to fix it

Add the operation to the owning class, or expose the value through a property or method that states the contract.

## Precision

Strict **0%**, lenient **9%** over 33 sampled findings on the audit corpus ([method](../precision.md#method)).

## Elsewhere

- Pylint W0212 protected-access
- Ruff SLF001

## Suppress a confirmed non-issue

`# spaghetti-ignore[encapsulation-violation]: reason` on the flagged line or the line above.
