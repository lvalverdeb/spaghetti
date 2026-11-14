# Precision audit (2026-11-13)

Every default-on rule was measured against five well-maintained public Python codebases
before it was allowed to stay on by default. This is the ROADMAP Phase 1 "noise audit";
rerun it whenever a rule changes.

## Method

- Corpus, shallow clones at the commits listed: `httpx` b5addb6, `rich` 3a109cc,
  `fastapi` 5758a9c, `pydantic` 560f0f9, `black` cf3e093. Package directories only
  (no tests).
- Scan with spaghetti 1.4.0 at commit 3510e6b, all rules on, default thresholds.
- Sample up to 8 findings per rule per repo, deterministically (seeded by repo name),
  762 findings in all. Each sample carries the code around the finding.
- Classify each sample from the point of view of an experienced maintainer of that repo:
  **TP** (matches the rule's intent and is worth at least considering), **FP** (misfire, or
  a technicality no maintainer would act on), **debatable** (style preference). Each FP gets a
  short pattern label so misfires can be counted by shape across repos. Classification was
  done by one reviewer per repo working from the snippets, with the source at hand.
- **Strict precision** = TP / sampled. **Lenient precision** = (TP + debatable) / sampled.
- Decide per rule: fix (a targeted narrowing of the detector), demote (ship off by default,
  still available via `[tool.spaghetti.rules]`), or keep.

The classification files and samples are not committed; the method is reproducible from
the corpus commits above.

## Headline

Before calibration the corpus scored 12 % strict / 33 % lenient precision overall, and four
of five mature libraries graded **C**. That is a noise problem, not a spaghetti problem.
After calibration the same corpus drops from 2727 findings to 940, httpx grades **A**, and
the rules that carried real signal (`high-complexity`, `long-function`, `deep-nesting`,
`god-class`, `long-file`, `todo-marker`) are untouched.

| Repo | Files | Lines | Before: grade / score / findings | After: grade / score / findings |
|---|---|---|---|---|
| httpx | 23 | 8827 | C / 73.3 / 222 | A / 90.6 / 56 |
| rich | 100 | 38348 | C / 71.9 / 771 | B / 89.1 / 208 |
| fastapi | 52 | 22801 | B / 79.1 / 419 | B / 89.5 / 142 |
| pydantic | 55 | 29755 | C / 74.8 / 799 | B / 82.8 / 328 |
| black | 25 | 16712 | C / 61.3 / 516 | C / 67.7 / 206 |
| **total** | | | **2727** | **940** |

## Per-rule results

Sorted by strict precision. "Findings (after)" is the count on the same corpus after the
changes below; 0 for a rule now off by default.


| Rule | Findings (before) | Sampled | TP | FP | Debatable | Strict precision | Lenient precision | Findings (after) |
|---|---|---|---|---|---|---|---|---|
| `todo-marker` | 70 | 22 | 19 | 2 | 1 | 86% | 91% | 70 |
| `deep-nesting` | 50 | 32 | 15 | 6 | 11 | 47% | 81% | 50 |
| `missing-return-type` | 7 | 7 | 3 | 2 | 2 | 43% | 71% | 7 |
| `high-complexity` | 250 | 40 | 16 | 2 | 22 | 40% | 95% | 250 |
| `long-function` | 167 | 39 | 14 | 13 | 12 | 36% | 67% | 167 |
| `untyped-dict` | 3 | 3 | 1 | 2 | 0 | 33% | 33% | 3 |
| `god-class` | 36 | 32 | 9 | 6 | 17 | 28% | 81% | 36 |
| `long-file` | 88 | 39 | 9 | 15 | 15 | 23% | 62% | 88 |
| `excessive-returns` | 31 | 20 | 2 | 9 | 9 | 10% | 55% | 31 |
| `duplicate-function-body` | 23 | 23 | 1 | 13 | 9 | 4% | 43% | 11 |
| `boolean-flag-params` | 89 | 33 | 1 | 29 | 3 | 3% | 12% | 16 |
| `too-many-params` | 222 | 40 | 1 | 29 | 10 | 2% | 28% | 9 |
| `magic-number` | 459 | 40 | 1 | 33 | 6 | 2% | 18% | 0 |
| `duplicate-branch` | 1 | 1 | 0 | 0 | 1 | 0% | 100% | 1 |
| `mutable-default` | 1 | 1 | 0 | 0 | 1 | 0% | 100% | 1 |
| `scope-mutation` | 16 | 16 | 0 | 1 | 15 | 0% | 94% | 3 |
| `missing-param-type` | 7 | 7 | 0 | 1 | 6 | 0% | 86% | 7 |
| `high-coupling` | 4 | 4 | 0 | 2 | 2 | 0% | 50% | 2 |
| `message-chain` | 8 | 8 | 0 | 4 | 4 | 0% | 50% | 8 |
| `orphan-interface` | 2 | 2 | 0 | 1 | 1 | 0% | 50% | 2 |
| `god-module` | 12 | 12 | 0 | 7 | 5 | 0% | 42% | 5 |
| `deep-inheritance` | 18 | 11 | 0 | 9 | 2 | 0% | 18% | 3 |
| `encapsulation-violation` | 157 | 33 | 0 | 30 | 3 | 0% | 9% | 108 |
| `swallowed-exception` | 44 | 30 | 0 | 28 | 2 | 0% | 7% | 26 |
| `magic-string` | 198 | 37 | 0 | 35 | 2 | 0% | 5% | 0 |
| `potential-circular-import` | 31 | 26 | 0 | 25 | 1 | 0% | 4% | 0 |
| `bare-except` | 1 | 1 | 0 | 1 | 0 | 0% | 0% | 1 |
| `dead-code` | 3 | 3 | 0 | 3 | 0 | 0% | 0% | 0 |
| `excessive-decorators` | 2 | 2 | 0 | 2 | 0 | 0% | 0% | 2 |
| `global-mutable` | 40 | 24 | 0 | 24 | 0 | 0% | 0% | 3 |
| `import-cycle` | 59 | 16 | 0 | 16 | 0 | 0% | 0% | 3 |
| `lazy-class` | 124 | 27 | 0 | 27 | 0 | 0% | 0% | 19 |
| `missing-else` | 214 | 40 | 0 | 40 | 0 | 0% | 0% | 0 |
| `pass-through-method` | 135 | 38 | 0 | 38 | 0 | 0% | 0% | 0 |
| `star-import` | 20 | 12 | 0 | 12 | 0 | 0% | 0% | 0 |
| `sync-async-duplication` | 8 | 8 | 0 | 8 | 0 | 0% | 0% | 8 |
| `unused-import` | 127 | 33 | 0 | 33 | 0 | 0% | 0% | 0 |
| **all rules** | 2727 | 762 | 92 | 508 | 162 | 12% | 33% | 940 |

## Decisions

### Bugs fixed

- **`import-cycle` / `high-coupling`**: imports inside `if __name__ == "__main__":` were
  counted as import-time edges (rich: console → pager → console was not a cycle at all).
  `from pkg import name` now resolves to the submodule `pkg.name` when one exists, so the
  ubiquitous `__init__` re-export pattern no longer forms a cycle. 59 → 3 findings; the
  three survivors (fastapi) import names defined in the package root from modules the root
  imports, a genuine import-order dependency.
- **`unused-import`**: names used only in string annotations (`"Console"`) and the explicit
  re-export idiom `from x import A as A` were flagged. Both fixed.
- **`dead-code`**: `raise ...; yield` is the idiom that turns a function into a generator,
  not dead code.

### Rules narrowed

- **`too-many-params`** counts only *required positional* parameters (no default, not
  keyword-only, not `self`/`cls`). Keyword-only and defaulted options are how Python APIs
  offer many independent options; the Parameter Object smell is positional confusion.
  222 → 9.
- **`boolean-flag-params`** counts only positional boolean defaults; a keyword-only flag
  cannot produce `f(x, True, False)` at a call site. 89 → 16.
- **`lazy-class`** exempts any class with a base, class keyword, or decorator (marker types,
  config subclasses, Protocols, TypedDicts, Pydantic models). The smell is a bare `class X:`
  holding one function. 124 → 19.
- **`global-mutable`** exempts `UPPER_CASE` names: lookup tables and name sets are constants
  by convention. 40 → 3.
- **`deep-inheritance`** skips exception hierarchies, which are taxonomies by design. 18 → 3.
- **`encapsulation-violation`** allows `other._x` inside a class that itself assigns
  `self._x` (`__eq__`, `merge`, `copy`). 157 → 108. Still the weakest default-on rule;
  the remaining pattern is access to a third-party library's private member, which is a real
  coupling risk, so it stays at `info`.
- **`swallowed-exception`** ignores handlers that carry a comment, treating them as a
  documented decision (the SonarQube convention). 44 → 26.
- **`scope-mutation`** flags `global` only; a `nonlocal` accumulator in a closure is scoped
  and idiomatic. 16 → 3.
- **`duplicate-function-body`** skips dunder methods (`__repr__`/`__eq__` across sibling
  classes are protocol hooks). 23 → 11.
- **`star-import`** skips `__init__.py` re-exports.

### Rules now off by default

0 % strict precision across the corpus, or owned by Ruff. Each can be switched on with
`[tool.spaghetti.rules]` (`magic-number = "info"`).

| Rule | Reason |
|---|---|
| `missing-else` | 0/40. A plain conditional update has no meaningful negative path; the rule is unsound as designed. |
| `pass-through-method` | 0/38. Every hit was an intended public delegate (Facade). |
| `magic-string` | 0/37. Hits were domain vocabulary: schema keywords, token strings, Python keywords. |
| `magic-number` | 1/40. Index arithmetic and obvious constants dominate. SonarQube also keeps its magic-number rule out of the default profile. |
| `potential-circular-import` | 0/26. The parent-import heuristic is superseded by real `import-cycle` detection. |
| `unused-import` | 0/33 before the fixes above; Ruff F401 owns this (ROADMAP non-goal). |
| `star-import` | 0/12; Ruff F403 owns this. |

### Kept as designed

- **`sync-async-duplication`** was classed 0/8 as "inherent" duplication. That is a
  disagreement with the rule's premise rather than a misfire: the rule exists precisely to
  surface twins so a fix applied to one reaches the other. It stays on; the fix hint, not the
  detection, is what a maintainer acts on.
- **`god-class`**, **`long-function`**, **`high-complexity`**, **`deep-nesting`**,
  **`long-file`**: the debatable share is "barely over threshold", which is a threshold
  question (configurable since Phase 1) rather than a precision one. McCabe-exact
  complexity lands in Phase 2 (ROADMAP D3).
- Rules with fewer than 10 samples (`god-module`, `message-chain`, `high-coupling`,
  `orphan-interface`, `excessive-decorators`, `bare-except`, `mutable-default`,
  `duplicate-branch`, `untyped-dict`) had too little data to decide and are unchanged.

## Caveats

- One classifier per repo, working mostly from snippets. The verdicts are consistent in
  direction across five independent passes, which is what the decisions rest on; individual
  TP/FP calls are not authoritative.
- The corpus is five well-maintained libraries. On a codebase that actually has structural
  problems the TP rate of the kept rules will be higher; the point of the audit is the FP
  floor, not the TP ceiling.

## Addendum: Phase 2 rules (2026-11-13)

Phase 2 added three rules and made `high-complexity` McCabe-exact. Same corpus, same
commits, scanned with spaghetti 2.0.0.

| Repo | Before Phase 2: grade / score / findings | After: grade / score / findings | MI (mean) | high-complexity | high-cognitive-complexity | duplicate-code | low-maintainability |
|---|---|---|---|---|---|---|---|
| httpx | A / 90.6 / 56 | B / 84.8 / 176 | 61.7 | 8 | 9 | 111 | 1 |
| rich | B / 89.1 / 208 | B / 80.7 / 456 | 72.3 | 52 | 55 | 188 | 3 |
| fastapi | B / 89.5 / 142 | B / 81.8 / 364 | 76.0 | 31 | 25 | 196 | 2 |
| pydantic | B / 82.8 / 328 | C / 70.4 / 615 | 59.6 | 70 | 67 | 215 | 4 |
| black | C / 67.7 / 206 | D / 47.0 / 339 | 49.8 | 94 | 76 | 47 | 6 |

- **`high-complexity`** moved from an approximation to Radon's exact rules (3211/3211
  corpus functions agree with `radon cc`). Corpus findings 250 → 255; the message is
  unchanged, so baselines carry over except where the count changed.
- **`high-cognitive-complexity`** (Sonar default threshold 15) fires 232 times. A spot
  check of 15 findings across the five repos (3 per repo, seeded sample) found functions
  of 27 to 375 lines with scores 16 to 151; the three borderline hits (16-18) are 30-50
  line functions with a loop-in-conditional shape a reviewer would still call dense. The
  rule is the published Sonar definition and ships at `warning`. Against the ROADMAP acceptance criterion: the SonarCloud comparison on a
  50-function sample could not be run offline; the implementation is tested against the
  worked examples in the specification instead.
- **`low-maintainability`** (MI < 10, Radon rank C) fires 16 times, every one a
  1000+-line core module (`rich/console.py`, `fastapi/routing.py`, `pydantic/types.py`,
  `black/__init__.py`). True positives by definition of the metric; whether a maintainer
  acts on them is a product decision, which is why the threshold is Radon's lowest rank.
- **`duplicate-code`** fires 757 times at 50 tokens. A spot check of 15 found no
  misfires — every region is a genuine copy — but most are *deliberate* copies: httpx's
  sync/async client and transport pairs, FastAPI's `APIRouter`/`FastAPI` mirrored method
  signatures, Black's parallel transformer classes. A per-block `warning` dropped every
  corpus repo to grade C. Duplication is a density metric in SonarQube and Qlty, not a
  defect, so the rule ships at `info` (grades recover to A/B/B/C/D) and remains available
  at `warning` via `[tool.spaghetti.rules]` for teams that gate on it. Known limitation:
  table-like data (long lists of similar calls) with varying arity can still form a
  region; strictly periodic windows are already skipped.

## Addendum: Phase 5 audit of the newer rules (2026-11-13)

The three Phase 2 rules had only spot checks above. This pass samples them the Phase 1 way
on a wider corpus: the five audit repos plus `django` and `airflow` (package directories
only), all at the commits pinned in `scripts/corpus.lock`, scanned with spaghetti 2.3.0 and
the Rust port at parity, default thresholds. 30 findings per rule, seeded sample
(`20261113`), classified from the code around each finding with the same TP / FP / debatable
scale. `contract-violation` has no corpus findings because no corpus repo declares contracts;
its behaviour is fixed by the fixtures in `tests/test_contracts.py` and it carries no
precision figure.

| Rule | Findings (before) | Sampled | TP | FP | Debatable | Strict precision | Lenient precision | Findings (after) |
|---|---|---|---|---|---|---|---|---|
| `high-cognitive-complexity` | 869 | 30 | 24 | 0 | 6 | 80% | 100% | 869 |
| `low-maintainability` | 51 | 30 | 24 | 0 | 6 | 80% | 100% | 51 |
| `duplicate-code` | 3390 | 30 | 9 | 13 | 8 | 30% | 57% | 1891 |

- **`high-cognitive-complexity`**: scores in the sample run from 16 to 148. No misfires;
  every function nests loops inside conditionals inside try blocks the way the metric
  describes. The six debatable hits score 16 or 17, two over the Sonar default of 15, and
  are 30-60 line functions a reviewer would call dense rather than broken: a threshold
  question, so the threshold stays configurable and the rule is unchanged.
- **`low-maintainability`**: every hit is a core module, 700 to 6500 lines, and 22 of 30
  sit at MI 0.0 because the formula clips below zero, so the message no longer separates a
  1300-line module from a 6500-line one. The six debatable hits are the modules under 1300
  lines (`django/db/models/constraints.py` at 699 lines is the smallest), where "split it
  along its responsibilities" is advice the maintainers have presumably already weighed.
  Unchanged; a graded message is possible later but changes no verdicts.
- **`duplicate-code`**: the spot check above said "no misfires, mostly deliberate copies";
  a proper sample disagrees. 13 of 30 were not duplicated *logic* at all: Alembic migration
  headers and `op.drop_table` runs, Airflow's `ConfigChange(...)` tables and `Arg(...)`
  declarations, import blocks shared by sibling route modules, FastAPI's parameter
  forwarding (`params.Cookie(default=default, alias=alias, ...)`), and windows that ran
  from the tail of one method into the head of the next because the two neighbours were
  shaped alike. Two narrowings follow, both mirrored in the Rust port:
  - A clone must contain at least one *logic* token (branch, loop, `try`, `with`,
    `return`, `raise`, comparison, boolean operator, comprehension, lambda or augmented
    assignment). A window of only calls, assignments, literals and imports is a declaration
    table or argument forwarding.
  - Definitions carry a name-bearing boundary token, so a window can run from one
    function or class into the next only when both sides enter a definition of the same
    name. Visitor methods or route handlers that merely sit next to each other no longer
    fuse into one clone.

  On the seven repos this drops the rule from 3390 to 1891 findings. Of the 30 judged
  samples the filter removed 9 of the 13 false positives, 3 of the 8 debatable ones and 1
  of the 9 true positives (an Airflow region that was mostly `mapped_column` declarations
  with a `return` on its first line). The 17 survivors score 8 TP / 5 debatable / 4 FP
  (47% strict, 76% lenient), a biased re-count rather than a fresh sample; the remaining
  false positives are migration files whose `upgrade()` bodies really are identical
  statements (`with op.batch_alter_table(...)` blocks), which the metric cannot tell from
  a copy a maintainer should factor out. The rule stays at `info`.

## Addendum: nesting depth recount (2.6.0, 2026-11-14)

`deep-nesting`, `excessive-returns` and `long-function`'s flat-nesting exemption share one depth
count, which until 2.5.0 added a level for every `except` handler on top of its `try` and for
every `elif` as if it were an `if` nested in the `else`, while ignoring `async for`, `async with`,
`try*`, `match` and `case`. Depth now follows indentation. On the seven audit repos:

| Rule | 2.5.0 | 2.6.0 |
|---|---|---|
| `deep-nesting` | 179 | 49 |
| `excessive-returns` | 85 | 51 |
| `long-function` | 712 | 593 |

A seeded sample of the django findings that disappeared shows the pattern the Phase 1 audit
had filed as "debatable, barely over threshold": `localize_input()` was reported at depth 6 and
is a five-branch `if`/`elif` chain, depth 1; `get_db_converters()` at depth 7 is a six-branch
chain, depth 2. The `long-function` findings that disappear are functions of 50-65 lines whose
real depth is 2, which is exactly the exemption's definition of a flat pipeline; whether 2 is
the right ceiling for that exemption is a threshold question (`LONG_FUNCTION_FLAT_NESTING_MAX`),
not a counting one. The Phase 1 precision rows above were measured under the old count and are
not restated; `deep-nesting`'s 47 % strict figure should be read as a floor.
