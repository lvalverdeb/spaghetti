# spaghetti

Spaghetti-code and architectural-smell detector for the Boti workspace.

Scans workspace packages for anti-patterns, architectural violations, and structural code smells — from single-function issues (long functions, deep nesting, high cyclomatic complexity) up to whole-package issues that only show up once you can see across files: real circular imports (not just a parent/child heuristic), copy-pasted function bodies, and the sync/async "twin" duplication pattern (`load`/`aload`, `foo`/`foo_async`) where a fix applied to one twin silently never reaches the other.

Every requested package is reviewed concurrently — one `boti.core.Agent` (`SpaghettiReviewAgent`) per package running its scan via `asyncio.to_thread` — then folded into a single consolidated report.

`spaghetti` is not tied to the Boti workspace: the package registry it scans is generic and configurable via a YAML file, ad-hoc CLI flags, or both — see [Configuring packages](#configuring-packages).

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
| `--plan` | off | Output a prioritized remediation plan instead of the standard report |

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

With `--plan`, the detector outputs a prioritized fix order instead of the standard report. Each rule is scored by `severity_weight × fix_effort` and grouped into priority levels (P0–P3):

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

## Configuring packages

With no flags, `spaghetti` scans this workspace's own `DEFAULT_PACKAGES` (`boti`, `boti-data`, `boti-dask`; see `src/spaghetti/detector.py`). To point it at other packages — in this workspace, another workspace, or any directory on disk — use `--config` and/or `--package`.

**Precedence:**
1. Neither flag given → the built-in defaults are used as-is.
2. `--config` given → its `packages:` mapping **replaces** the defaults entirely, so a config file states the full set explicitly rather than silently inheriting unrelated hardcoded packages.
3. `--package NAME=PATH` entries are then overlaid on top of whichever set (1) or (2) produced — adding new names or overriding ones already defined, so a config file plus a quick ad-hoc addition both work together.

### `--config`: YAML file

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

### `--package`: ad-hoc CLI entries

```bash
uv run spaghetti --package my-lib=my-lib/src/my_lib --package other=../other/src/other
```

Repeatable; paths resolve relative to the current directory. Combine with `--config` to override or extend a config file for one run without editing it.

## Development

```bash
uv run pytest spaghetti/tests/
```
