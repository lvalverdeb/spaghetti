# spaghetti-detector

Spaghetti-code and architectural-smell detector.

Scans workspace packages for anti-patterns, architectural violations, and structural code smells — from single-function issues (long functions, deep nesting, high cyclomatic complexity) up to whole-package issues that only show up once you can see across files: real circular imports (not just a parent/child heuristic), copy-pasted function bodies, and the sync/async "twin" duplication pattern (`load`/`aload`, `foo`/`foo_async`) where a fix applied to one twin silently never reaches the other.

Every requested package is reviewed concurrently — one agent per package and then folded into a single consolidated report.

The package registry is generic and configurable via a YAML file, ad-hoc CLI flags, or both — see [Configuring Packages](#configuring-packages).

## Why It Exists

AI-generated spaghetti code — often referred to as "slop code" — is extremely common because Large Language Models (LLMs) prioritise immediate functional completion (the "happy path") over long-term software architecture. Whilst it looks syntactically perfect and heavily commented, it often suffers from structural issues:

- **Monolithic structures:** AI tends to dump vast amounts of logic into single, giant files rather than separating concerns.
- **Copy-paste duplication:** Instead of refactoring code into reusable functions, LLMs often repeat the same block of code with minor variations.
- **Accidental complexity:** Because AI lacks system-wide perspective, it wires features together in a highly coupled way.
- **Hallucinated dependencies:** A significant risk where AI suggests the use of non-existent libraries or packages.

This prevalence stems from the fundamental nature of AI training: LLMs are optimised to predict the next logical token based on probability, not to design maintainable software. While an AI can produce a functioning script quickly, it lacks the intuitive foresight that experienced developers use to build modular and scalable applications.

Human-written spaghetti code is extremely common and has existed since the dawn of programming. Whilst AI creates messy code due to a lack of situational awareness, humans usually create it due to time pressure, changing requirements, or a lack of experience.

### Why Humans Write Spaghetti Code

- **Tight deadlines:** Developers rush to ship features, prioritising speed over clean architecture.
- **Scope creep:** Constantly adding new features to an old system without rewriting the base structure.
- **Skill gaps:** Junior developers may not yet understand design patterns or how to separate concerns.
- **The "copy-paste" habit:** Reusing working blocks of code across a project instead of building reusable functions.
- **Lack of code reviews:** Teams skipping peer reviews, allowing messy logic to slip into production.

### AI vs. Human Spaghetti Code

- **Human style:** Often features massive nested loops, confusing variable names (like `x` or `data1`), and forgotten `TODO` notes.
- **AI style:** Usually looks highly professional, has perfect indentation, and includes beautiful comments, but the underlying logic is deeply tangled and redundant.

## How spaghetti-detector Helps

Every problem above maps to one or more mechanically-enforced rules. The detector does not guess — it measures concrete thresholds and reports exact violations.

### Problem → Rule Mapping

| Problem | Detector Rules | What It Catches |
|---------|---------------|-----------------|
| **Monolithic structures** | `god-class`, `god-module`, `long-function`, `long-file`, `deep-nesting` | Classes with 25+ methods, files over 400 lines, functions exceeding 50 lines, nesting beyond 5 levels |
| **Copy-paste duplication** | `duplicate-function-body`, `sync-async-duplication` | Identical function bodies (5+ lines), sync/async twin pairs with ≥60% text similarity |
| **Accidental complexity** | `high-complexity`, `excessive-returns`, `message-chain`, `deep-inheritance`, `excessive-decorators` | Cyclomatic complexity above 10, functions with 4+ return paths, chained calls deeper than 3 levels, inheritance exceeding 4 levels |
| **Layering violations** | `layer-violation`, `transport-in-library`, `import-cycle`, `encapsulation-violation` | Library code importing transport frameworks, circular import chains, accessing private attributes across objects |
| **Type safety gaps** | `missing-return-type`, `missing-param-type`, `untyped-dict`, `bare-except` | Public functions missing annotations, bare `dict` in type hints, bare `except:` clauses |
| **Dead code & clutter** | `dead-code`, `unused-import`, `star-import`, `todo-marker`, `magic-number` | Unreachable statements after `return`/`raise`/`break`, `from x import *`, unexplained numeric literals |

### From Detection to Remediation

The detector produces a consolidated report with a health score and grade per package:

```
  Package          Grade   Score   Files   KLOC   Issues
  ──────────────── ───── ───────  ────── ────── ───────
  boti-data           B    78.3       18   3.2       12
  etl-core            A    92.1       14   2.8        5
  OVERALL             B    82.5       32   6.0       17
```

Use `--plan` to get a prioritised remediation roadmap scored by `severity_weight × fix_effort`:

```bash
uv run spaghetti --plan --top 10
```

```
  #   Pri  Rule                           Sev  Effort     Issues  Score
  ─── ──── ────────────────────────────── ──── ───────── ──────  ─────
  1   P0   import-cycle                   ✖    major        3   30.0
  2   P0   god-class                      ✖    major        2   30.0
  3   P1   high-complexity                ⚠    moderate     5   15.0
  4   P1   long-function                  ⚠    moderate     4   12.0
```

This ensures you fix structural issues (circular imports, god-classes) before cosmetic ones (missing type hints, magic numbers) — maximising impact per unit of effort.

## Usage

```bash
uv run spaghetti
uv run spaghetti --packages boti-data boti-dask
uv run spaghetti --severity error
uv run spaghetti --top 10 --exclude tests/ examples/
uv run spaghetti --json > report.json
uv run spaghetti --plan --top 10
uv run spaghetti --config spaghetti.yaml
uv run spaghetti --package my-lib=my-lib/src/my_lib
```

Exit codes: `0` (clean), `1` (warnings present), `2` (errors present) — safe to wire into CI as a gate.

### Options

| Flag | Default | Description |
| --- | --- | --- |
| `--config` | none | YAML file with a `packages: {name: path}` mapping (see below); replaces the built-in defaults |
| `--package` | none | Add or override one package as `NAME=PATH` (repeatable); applied on top of `--config` or the defaults |
| `--packages` | all resolved packages | Names to scan from the resolved registry |
| `--severity` | `info` | Minimum severity to display (`info` / `warning` / `error`) |
| `--json` | off | Output as JSON instead of the console report |
| `--top` | `5` | Number of worst files to list per package |
| `--exclude` | none | Path substrings to exclude from scanning |
| `--min-duplicate-lines` | `5` | Minimum function length to consider for duplicate-body detection |
| `--twin-similarity` | `0.6` | Minimum text-similarity ratio (0–1) to flag a sync/async twin pair |
| `--plan` | off | Output a prioritised remediation plan instead of the standard report |

Run `uv run spaghetti --help` for the full list.

## Inline Suppression

Suppress specific findings on a line with `# spaghetti-ignore[rule]`:

```python
# Suppress a specific rule
def f():  # spaghetti-ignore[long-function]: intentionally large
    ...

# Suppress all rules on a line
x: dict = {}  # spaghetti-ignore: reviewed, no issue
```

The marker applies to the line it appears on and the line directly above it (so a marker can sit above a `def` line too long for a trailing comment). Suppressed findings are counted in the report (`suppressed: N` in the header) rather than silently dropped — they remain visible.

## JSON Output

With `--json`, the report is a single JSON object to stdout:

```json
{
  "issues": [
    {
      "file": "src/my_module.py",
      "line": 42,
      "severity": "warning",
      "rule": "long-function",
      "message": "my_func() is 65 lines (max 50)",
      "package": "my-lib"
    }
  ],
  "suppressed": 3
}
```

## Remediation Plan

With `--plan`, the detector outputs a prioritised fix order instead of the standard report. Each rule is scored by `severity_weight × fix_effort` and grouped into priority levels (P0–P3):

```bash
uv run spaghetti --plan --top 10
```

**Priority levels:**
- **P0** (score ≥ 12): CRITICAL — fix immediately (e.g., circular imports, god-classes)
- **P1** (score ≥ 7): HIGH — fix this sprint
- **P2** (score ≥ 3): MEDIUM — plan for next cycle
- **P3** (score < 3): LOW — track in backlog

The plan groups issues by rule, counts affected files, and lists a recommended fix order. This makes it easy to start a code-quality improvement cycle with the highest-impact fixes first.

## Rules

The detector checks **36 rules** across four tiers:

**Per-file AST checks (30 rules):** `long-function`, `high-complexity`, `missing-return-type`, `missing-param-type`, `too-many-params`, `excessive-returns`, `boolean-flag-params`, `deep-nesting`, `untyped-dict`, `unused-import`, `swallowed-exception`, `duplicate-branch`, `encapsulation-violation`, `god-class`, `layer-violation`, `transport-in-library`, `potential-circular-import`, `god-module`, `mutable-default`, `bare-except`, `star-import`, `global-mutable`, `scope-mutation`, `dead-code`, `message-chain`, `excessive-decorators`, `magic-number`, `missing-else`, `lazy-class`, `deep-inheritance`.

**Per-file source-text checks (2 rules):** `long-file`, `todo-marker`.

**Infrastructure checks (1 rule):** `syntax-error` (files that fail `ast.parse()`).

**Per-package cross-file checks (3 rules):** `import-cycle`, `duplicate-function-body`, `sync-async-duplication`.

See [SDD.md](SDD.md) for the full rule catalog, thresholds, and scoring formula.

## Configuring Packages

With no flags, `spaghetti` scans this workspace's own `DEFAULT_PACKAGES` (`boti`, `boti-data`, `boti-dask`; see `src/spaghetti/detector.py`). To point it at other packages — in this workspace, another workspace, or any directory on disk — use `--config` and/or `--package`.

**Precedence:**
1. Neither flag given → the built-in defaults are used as-is.
2. `--config` given → its `packages:` mapping **replaces** the defaults entirely, so a config file states the full set explicitly rather than silently inheriting unrelated hardcoded packages.
3. `--package NAME=PATH` entries are then overlaid on top of whichever set (1) or (2) produced — adding new names or overriding ones already defined, so a config file plus a quick ad-hoc addition both work together.

### `--config`: YAML File

```yaml
# spaghetti.yaml
packages:
  my-lib: my-lib/src/my_lib
  my-service: services/my-service/src/my_service
```

Paths resolve **relative to the config file's own directory**, not the caller's working directory, so the same config works no matter where you invoke `spaghetti` from.

```bash
uv run spaghetti --config spaghetti.yaml
```

### `--package`: Ad-hoc CLI Entries

```bash
uv run spaghetti --package my-lib=my-lib/src/my_lib --package other=../other/src/other
```

Repeatable; paths resolve relative to the current directory. Combine with `--config` to override or extend a config file for one run without editing it.

## Development

```bash
uv run pytest spaghetti/tests/
```
