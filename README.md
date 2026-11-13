# spaghetti-detector

Spaghetti code and architectural-smell detector.

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
| **Dead code & clutter** | `dead-code`, `unused-import`, `star-import`, `todo-marker`, `magic-number`, `magic-string` | Unreachable statements after `return`/`raise`/`break`, `from x import *`, unexplained numeric literals, repeated string comparisons standing in for a category code |

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
uv run spaghetti --packages package1 package2
uv run spaghetti --severity error
uv run spaghetti --top 10 --exclude tests/ examples/
uv run spaghetti --json > report.json
uv run spaghetti --plan --top 10
uv run spaghetti --config spaghetti.yaml
uv run spaghetti --package my-lib=my-lib/src/my_lib
uv run spaghetti -q
uv run spaghetti --update-baseline           # accept today's findings; see Baseline
uv run spaghetti --diff-base origin/main     # only findings on lines this branch changed
uv run spaghetti --format sarif > spaghetti.sarif
uv run spaghetti --fail-on error
```

Exit codes: `0` (clean), `1` (warnings present), `2` (errors present). `--fail-on` changes which
severities count (`error`, `warning` = default, `info`, `never`). Findings hidden by a baseline or
by `--diff-base` never affect the exit code.

### Options

| Flag | Default | Description |
| --- | --- | --- |
| `--config` | none | YAML file with a `packages: {name: path}` mapping (see below); replaces the built-in defaults |
| `--package` | none | Add or override one package as `NAME=PATH` (repeatable); applied on top of `--config` or the defaults |
| `--packages` | all resolved packages | Names to scan from the resolved registry |
| `--severity` | `info` | Minimum severity to display (`info` / `warning` / `error`) |
| `--json` | off | Output as JSON instead of the console report |
| `--quiet` / `-q` | off | Suppress the report banner (text-report mode only; `--json` is unaffected) |
| `--top` | `5` | Number of worst files to list per package |
| `--exclude` | none | Path substrings to exclude from scanning |
| `--min-duplicate-lines` | `5` | Minimum function length to consider for duplicate-body detection |
| `--twin-similarity` | `0.6` | Minimum text-similarity ratio (0–1) to flag a sync/async twin pair |
| `--plan` | off | Output a prioritised remediation plan instead of the standard report |
| `--format` | `text` | `text`, `json`, or `sarif` (SARIF 2.1.0 for code-scanning tools) |
| `--fail-on` | `warning` | Lowest severity that makes the exit code non-zero; `never` always exits 0 |
| `--baseline` | from settings | Baseline file of accepted findings; see [Baseline](#baseline) |
| `--update-baseline` | off | Write every current finding to the baseline file and exit 0 |
| `--diff-base` | none | Git ref; report only findings on lines added or changed since it |
| `--no-pyproject` | off | Ignore any `[tool.spaghetti]` table found above the working directory |
| `--version` | — | Print the installed spaghetti-detector version and exit |

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

With `--json`, the report is a single JSON object to stdout. Its shape is frozen as
**schema version 1** and validated in CI against [`schema/report.v1.json`](schema/report.v1.json);
fields may be added without a version bump, never removed or retyped.

```json
{
  "schema_version": 1,
  "summary": { "grade": "B", "score": 82.5, "files": 32, "lines": 6000, "functions": 410,
               "issues": 17, "errors": 2, "warnings": 11, "infos": 4,
               "maintainability_index": 64.2 },
  "packages": {
    "my-lib": { "grade": "A", "score": 92.1, "files": 14, "lines": 2800, "functions": 190,
                "issues": 5, "errors": 0, "warnings": 3, "infos": 2,
                "maintainability_index": 71.0 }
  },
  "issues": [
    {
      "file": "src/my_module.py",
      "line": 42,
      "column": 1,
      "end_line": 106,
      "end_column": 17,
      "severity": "warning",
      "rule": "long-function",
      "message": "my_func() is 65 lines (max 50)",
      "package": "my-lib",
      "fingerprint": "7ef53d04ad35db1d"
    }
  ],
  "suppressed": 3,
  "suppression_help": "Silence a confirmed non-issue by adding '# spaghetti-ignore[rule-name]: reason' on the flagged line (or the line above).",
  "ignored": [
    {
      "file": "src/legacy.py",
      "line": 10,
      "column": 5,
      "end_line": 10,
      "end_column": 12,
      "severity": "warning",
      "rule": "bare-except",
      "message": "Bare except: clause",
      "package": "my-lib",
      "fingerprint": "0d77fdfa750f165d",
      "reason": "reviewed, no issue"
    }
  ]
}
```

- `column` / `end_column` are 1-based character columns (`end_column` exclusive); they are
  `null` for rules without a precise location (e.g. `long-file`, `import-cycle`).
- `fingerprint` identifies a finding stably across line shifts and re-runs: a hash of the
  rule, the package-relative path, and the message with digits stripped. It is the key a
  baseline file will use.
- `ignored` lists the findings a `# spaghetti-ignore` marker actually silenced (each with its
  `reason`, if one was given), so a suppression is visible rather than invisible in the JSON
  report too.

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

The detector checks **43 rules** across five tiers:

**Per-file AST checks (32 rules):** `long-function`, `high-complexity` (McCabe cyclomatic complexity, identical to `radon cc`), `high-cognitive-complexity` (SonarSource cognitive complexity, default 15), `missing-return-type`, `missing-param-type`, `too-many-params`, `excessive-returns`, `boolean-flag-params`, `deep-nesting`, `untyped-dict`, `unused-import`, `swallowed-exception`, `duplicate-branch`, `encapsulation-violation`, `god-class` (also factors in WMC — total cyclomatic complexity summed across a class's methods — alongside method/attribute counts), `layer-violation`, `transport-in-library`, `potential-circular-import`, `god-module`, `mutable-default`, `bare-except`, `star-import`, `global-mutable`, `scope-mutation`, `dead-code`, `message-chain`, `excessive-decorators`, `magic-number`, `magic-string`, `missing-else`, `lazy-class`, `deep-inheritance`, `pass-through-method`.

**Per-file source-text checks (3 rules):** `long-file`, `todo-marker`, `low-maintainability` (SEI/Radon Maintainability Index under 10; the per-package mean is also reported as `maintainability_index`).

**Infrastructure checks (2 rules):** `syntax-error` (files that fail `ast.parse()`), `unreadable-file` (files that cannot be read at all — broken symlink, permission denied).

**Per-package cross-file checks (3 rules):** `orphan-interface` (an abstract class with exactly one implementation), `duplicate-function-body`, `sync-async-duplication`.

**Workspace checks (3 rules), run once over every registered package:** `import-cycle` and `high-coupling` on the union import graph, so a cycle between two packages is found; `duplicate-code`, a PMD-CPD-style type-2 clone detector over a normalised AST token stream that finds copies with renamed variables (default window 50 tokens, `min-clone-tokens`). Duplication ships at `info`: it is a density signal, and mature libraries carry deliberate copies (sync/async twins).

**Off by default** (switch on with `[tool.spaghetti.rules]`): `magic-number`, `magic-string`, `missing-else`, `pass-through-method`, `potential-circular-import`, `unused-import`, `star-import`, and `low-cohesion` (LCOM4). The first five scored at or near zero precision in the [2026-11 audit over five public codebases](docs/precision.md); the next two are Ruff's territory (F401/F403). The audit also documents why `too-many-params` counts only required positional parameters, why `lazy-class` exempts every subclass, and the other calibrations.

**2.0 metric break.** Cyclomatic complexity is now McCabe-exact (Radon's rules: `match` arms, ternaries, comprehension clauses, `try/else`, no `with`). Scores and `high-complexity`/`god-class` findings shift accordingly; regenerate baselines when upgrading from 1.x. Agreement with `radon cc` is 3211/3211 functions on the audit corpus (see [docs/precision.md](docs/precision.md)).

See [SDD.md](SDD.md) for the full rule catalog, thresholds, and scoring formula.

## Configuration

Settings live in `pyproject.toml` under `[tool.spaghetti]` (found by walking up from the
working directory) and/or in the YAML file passed to `--config`. Precedence, lowest to
highest: built-in defaults, `pyproject.toml`, `--config`, explicit CLI flags. A malformed
file is an error, never a silent fallback to defaults.

```toml
[tool.spaghetti]
exclude = ["tests/", "migrations/"]
fail-on = "error"                        # error | warning | info | never
baseline = ".spaghetti-baseline.json"    # relative to this file
library-packages = ["my-lib"]            # packages `transport-in-library` applies to

[tool.spaghetti.packages]                # same mapping as --config's `packages:`
my-lib = "src/my_lib"

[tool.spaghetti.thresholds]              # any numeric constant in spaghetti/config.py, kebab-case
max-function-lines = 60
complexity-threshold = 12
max-class-methods = 30

[tool.spaghetti.rules]                   # "off", or a severity override
magic-number = "off"
missing-return-type = "error"

[tool.spaghetti.layers.my-lib]           # path prefix inside the package -> forbidden import prefixes
"routes/" = ["my_lib.db."]
"domain/" = ["fastapi", "sqlalchemy"]
```

The same keys work in the `--config` YAML (`thresholds:`, `rules:`, `layers:`, ...). Unknown
keys, unknown rules, and unknown thresholds are rejected with the valid names listed.

## Baseline

A baseline lets a legacy codebase turn spaghetti on today with zero new findings and
ratchet down from there.

```bash
uv run spaghetti --baseline .spaghetti-baseline.json --update-baseline   # accept everything, once
uv run spaghetti --baseline .spaghetti-baseline.json                      # from now on: new findings only
```

Set `baseline` in `[tool.spaghetti]` and the flag becomes unnecessary. Findings are matched by
their `fingerprint` (rule + package-relative path + message with digits stripped), so they
survive line shifts and unrelated edits. Known findings are hidden from the report and the exit
code; baseline entries no longer matched are listed as **fixed**, so progress is visible. Commit
the baseline file and re-run `--update-baseline` when you want to move the ratchet.

## Changed Lines Only

```bash
uv run spaghetti --diff-base origin/main
```

Reports only findings whose span touches a line added or modified since the ref (working tree
included). This is the "new code" gate for pull requests. The summary states how many findings
the filter hid. Package-level findings attributed to an untouched file (an `import-cycle` on an
`__init__.py` you didn't edit) are hidden like any other.

## SARIF and GitHub Code Scanning

`--format sarif` emits SARIF 2.1.0: one result per finding with rule metadata, character
columns, the fingerprint under `partialFingerprints`, `baselineState` when a baseline is in use,
and inline `# spaghetti-ignore` suppressions as suppressed results with their justification.
Upload it with `github/codeql-action/upload-sarif` and findings appear as code-scanning alerts
and PR annotations.

### GitHub Action

```yaml
- uses: lvalverdeb/spaghetti@v1
  with:
    diff-base: ${{ github.base_ref }}    # optional: changed lines only on PRs
    fail-on: error
    # args: --package my-lib=src/my_lib   # when there is no [tool.spaghetti] table
```

Needs `security-events: write` to upload SARIF (set `upload-sarif: false` otherwise).

### pre-commit

```yaml
- repo: https://github.com/lvalverdeb/spaghetti
  rev: v1.5.0
  hooks:
    - id: spaghetti
      args: ["--diff-base", "HEAD"]
```

## Trend, Compare, Hotspots and the HTML Report

Linters answer "what is wrong now". These answer "is it getting better, what did this
change do, and where should the effort go".

```bash
spaghetti --snapshot                       # append this run to .spaghetti/snapshots.jsonl
spaghetti --compare latest                 # delta vs the last snapshot: score, introduced, fixed, files worse
spaghetti --compare ~3                     # vs three snapshots back; a SHA or timestamp prefix also works
spaghetti --hotspots                       # files ranked by health penalty × git change frequency
spaghetti --hotspots --plan                # the remediation plan, weighted by that churn
spaghetti --format html > report.html      # one self-contained page: scorecard, trend, hotspots, findings
```

- **Snapshots** record the git SHA, timestamp, per-package scores, per-file penalties and
  every finding's fingerprint. The store is a JSONL file (`--snapshot-store`, or
  `snapshot-store` in `[tool.spaghetti]`), so it diffs and merges like any text file.
- **Compare** matches findings by fingerprint, so a finding that moved lines is neither
  introduced nor fixed. The JSON report gains a `compare` object; the text and HTML reports
  a summary line plus the introduced and fixed lists. Run it in CI with `--snapshot` on the
  default branch and `--compare latest` on PRs.
- **Hotspots** are CodeScene's idea: a file that is both tangled and actively edited is
  where remediation pays off; a tangled file nobody touches can wait. Change frequency is
  commits touching the file in the last `--churn-months` (default 6) with the author count
  alongside. Each finding's priority in `--plan` is multiplied by a factor between 1 (file
  untouched) and 2 (the repository's most-edited file), log-scaled, so P0 means
  "worst *and* hottest".
- **HTML** needs no server or account. Trend sparklines come from the snapshot store when
  one exists.

## Configuring Packages

With no flags, `spaghetti` auto-discovers packages from the current directory: every immediate subdirectory containing at least one `.py` file becomes its own named package (skipping `.venv`, `.git`, `__pycache__`, `node_modules`, and similar noise directories), and any loose `.py` files sitting directly in the current directory are grouped into one more package named after the directory itself. To point it at other packages — in this workspace, another workspace, or any directory on disk — use `--config` and/or `--package`.

**Precedence:**
1. Neither flag given → cwd auto-discovery is used, as described above.
2. `--config` given → its `packages:` mapping is used as the full set, explicitly rather than auto-discovered.
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

Requires Python 3.11 or newer. The only runtime dependency is PyYAML.

```bash
uv sync --group dev
uv run pytest                       # unit tests
uv run ruff check src tests scripts
uv run python scripts/dogfood_gate.py   # spaghetti on itself: no errors, grade B or better
```

CI runs the suite on Python 3.11, 3.12 and 3.13, plus the dogfood gate. See
[ROADMAP.md](ROADMAP.md) for where the project is headed.
