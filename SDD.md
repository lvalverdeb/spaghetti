# Software Design Document — Spaghetti Code Detector

**Version:** 1.0.0
**Scope:** Generic, package-agnostic static analysis tool for detecting code smells, architectural violations, and structural anti-patterns in Python codebases.

---

## 1. Purpose

The spaghetti detector is a workspace-level quality gate. It scans Python packages for anti-patterns that indicate structural decay — long functions, deep nesting, high complexity, god-classes, god-modules, sync/async duplication, circular imports, copy-pasted function bodies, mutable defaults, shared mutable state, architectural layer violations, dead code, message chains, excessive decorators, magic numbers, missing else branches, lazy classes, and deep inheritance. It produces a severity-weighted health score (0–100) with letter grades (A–F) and exits with a non-zero code when errors or warnings are present, making it suitable for CI gating.

## 2. Architecture

```
┌──────────────────────────────────────────────────────────────────┐
│                         CLI Entry Point                          │
│  resolve_packages() → review_packages_concurrently() → report    │
└──────────┬──────────────────────────────────────┬───────────────┘
           │                                      │
           ▼                                      ▼
   ┌───────────────┐                    ┌──────────────────┐
    │  Per-File     │                    │  Per-Package     │
    │  AST Checks   │                    │  Cross-File      │
    │  (31 rules)   │                    │  Checks (3 rules)│
   └───────┬───────┘                    └────────┬─────────┘
           │                                     │
           ▼                                     ▼
   ┌───────────────┐                    ┌──────────────────┐
   │  Source-Text  │                    │  Import Graph    │
   │  Checks (2)   │                    │  DFS Cycle       │
   └───────────────┘                    └──────────────────┘
           │
           ▼
   ┌───────────────┐
   │  Infrastructure│
   │  Check (1)    │
   │  syntax-error │
   └───────────────┘
```

### 2.1 Concurrency Model

Each package is scanned by a dedicated `SpaghettiReviewAgent` (extends `boti.core.Agent`) running via `asyncio.to_thread`. All package scans run concurrently via `asyncio.gather`. Results are aggregated into a single `ScanResult` and rendered as text or JSON.

### 2.2 Scoring

```
penalty = Σ severity_weight(issue)          # error=6.0, warning=1.5, info=0.3
penalty_per_kloc = penalty / (total_lines / 1000)
score = max(0, 100 - penalty_per_kloc)
grade = A (≥90) | B (≥75) | C (≥60) | D (≥40) | F (<40)
```

Exit codes: 0 = clean, 1 = warnings present, 2 = errors present.

## 3. Rule Catalog

### 3.1 Per-File AST Checks (31 rules)

| Rule | Severity | Threshold | What It Catches |
|------|----------|-----------|-----------------|
| `long-function` | warning | >50 lines | Functions exceeding the line limit |
| `high-complexity` | warning/error | >10 (warn), >15 (error) | McCabe cyclomatic complexity |
| `missing-return-type` | warning | — | Public functions without return type annotation (skips `__init__` — its return type is implicitly `None`) |
| `missing-param-type` | info | — | Public function parameters without type annotation (skips `self`/`cls`) |
| `too-many-params` | warning | >6 params | Functions with excessive parameter counts |
| `excessive-returns` | info | >3 return stmts | Functions with too many return paths |
| `boolean-flag-params` | info | ≥3 boolean flags | Functions with combinatorial boolean parameters |
| `deep-nesting` | warning | >5 levels | Functions with deep if/for/while/with/try nesting |
| `untyped-dict` | info | — | Bare `dict` in type hints without parameters |
| `unused-import` | warning | — | Imported names never referenced (skips `__init__.py` — re-exports are legitimate) |
| `swallowed-exception` | warning | — | `except: pass` or `except: ...` blocks |
| `duplicate-branch` | warning | — | if/else blocks with structurally identical bodies |
| `encapsulation-violation` | info | — | Accessing private `_name` through non-self/cls |
| `god-class` | warning/error | >25 methods (error >37), >20 attrs (error >30) | Classes with too many methods or attributes |
| `layer-violation` | error | — | Imports forbidden by architectural layer rules |
| `transport-in-library` | error | — | Library packages importing transport frameworks |
| `potential-circular-import` | warning | — | Child importing parent within same package |
| `god-module` | warning | — | Module exposing >15 public symbols |
| `mutable-default` | warning | — | Functions with mutable default arguments |
| `bare-except` | warning | — | `except:` without specific exception type |
| `star-import` | warning | — | `from x import *` statements |
| `global-mutable` | info | — | Module-level mutable state (list/dict/set assigned at module scope) |
| `scope-mutation` | info | — | Functions that mutate outer-scope variables via `global`/`nonlocal` declarations |
| `dead-code` | warning | — | Statements unreachable after `return`/`raise`/`break`/`continue` |
| `message-chain` | info | >3 depth | Method/attribute chains exceeding 3 sequential accesses |
| `excessive-decorators` | info | >3 decorators | Functions or classes with more than 3 stacked decorators |
| `magic-number` | info | — | Numeric literals other than 0, 1, -1 (`__init__` skipped) |
| `magic-string` | info | ≥2 occurrences | The same string literal compared for equality in 2+ places — an ad-hoc category/status code |
| `missing-else` | info | — | `if` blocks with 2+ statements but no `else`/`elif` |
| `lazy-class` | info | <2 methods | Classes with 0 or 1 methods — prefer a plain function or `@dataclass` |
| `deep-inheritance` | warning | ≥4 depth | Effective inheritance chain depth exceeding 4 levels |

### 3.2 Per-File Source-Text Checks (2 rules)

| Rule | Severity | Threshold | What It Catches |
|------|----------|-----------|-----------------|
| `long-file` | warning | >400 lines | Files exceeding the line limit |
| `todo-marker` | info | — | TODO/FIXME/XXX/HACK comments |

### 3.3 Infrastructure Checks (1 rule)

| Rule | Severity | Threshold | What It Catches |
|------|----------|-----------|-----------------|
| `syntax-error` | error | — | Files that fail `ast.parse()` — blocks all deeper AST analysis |

> `syntax-error` is emitted before any `ALL_CHECKS` run. When a file has a syntax error, no other AST rules are applied to it.

### 3.4 Per-Package Cross-File Checks (3 rules)

| Rule | Severity | Threshold | What It Catches |
|------|----------|-----------|-----------------|
| `import-cycle` | error | — | Real circular import cycles (DFS on full import graph) |
| `duplicate-function-body` | warning | ≥5 identical lines | Byte-for-byte identical function bodies across files |
| `sync-async-duplication` | warning | ≥60% text similarity | Sync/async twin pairs (e.g. `load`/`aload`) |

### 3.5 Canonical AST Patterns for Tangled Code Detection

The detector's rules are grounded in **8 canonical AST structural patterns** that characterize tangled code. Each pattern maps to one or more detector rules:

#### Pattern 1: Deeply Nested Conditional (Arrow Anti-Pattern)

**AST Markers:** A chain of nested `If` / `For` / `While` / `With` / `Try` blocks where the body of one contains another control-flow node, drifting the code to the right.

**Detection Goal:** Flags excessive nesting depth (threshold: >5 levels). Indicates the Arrow Anti-Pattern — code structurally drifts too far right, making control flow impossible to follow.

**Covered by:** `deep-nesting`, `long-function`

```python
# BAD — depth=6, Arrow Anti-Pattern
def process(data):
    if data:
        for item in data:
            if item.valid:
                if item.active:
                    for sub in item.children:
                        if sub.ready:    # ← code has drifted far right
                            handle(sub)
```

#### Pattern 2: Cyclomatic Path Explosion

**AST Markers:** High concentration of branching nodes (`If`, `For`, `While`, `ExceptHandler`, `BoolOp`, `Assert`, comprehensions) within a single function subtree.

**Detection Goal:** Flags methods with too many distinct execution pathways (threshold: >10, error at >15). McCabe complexity counts linearly independent paths through a function — a function with complexity 15 has at least 15 independent execution paths, making manual reasoning about all branches impractical.

**Covered by:** `high-complexity`, `excessive-returns`

#### Pattern 3: God Object / Endless Subtree

**AST Markers:** A `ClassDef` or module root containing an excessive number of `FunctionDef` / `AsyncFunctionDef` child nodes, or a module with too many public symbols.

**Detection Goal:** Catches massive components that try to manage everything. Class threshold: >25 methods (error at >37), >20 attributes (error at >30). Module threshold: >15 public symbols.

**Covered by:** `god-class`, `god-module`, `long-file`

#### Pattern 4: Parameter Overflow

**AST Markers:** A `FunctionDef` / `AsyncFunctionDef` node where the total parameter count (positional + keyword-only + `*args` + `**kwargs`) exceeds 4–5 elements, or ≥3 have boolean defaults.

**Detection Goal:** Identifies tightly coupled logic. Functions with long parameter lists indicate missing encapsulation and fragile call sites.

**Covered by:** `too-many-params` (>6), `boolean-flag-params` (≥3 boolean flags)

#### Pattern 5: Shared Mutable State / Side-Effect

**AST Markers:** An `Assign` or `AugAssign` nested inside a function body that targets a name declared `global` or `nonlocal`, or a module-level mutable assignment.

**Detection Goal:** Catches untraceable variable overrides. When multiple independent functions mutate the same outer-lexical variable, tracing app state becomes a nightmare.

**Covered by:** `scope-mutation` (via `global`/`nonlocal` declarations), `global-mutable` (module-level mutable literals)

```python
# BAD — shared mutable state
counter = 0
def increment():
    global counter
    counter += 1         # ← mutates module-level state

# BETTER — encapsulated
class Counter:
    def __init__(self) -> None:
        self._value = 0
    def increment(self) -> None:
        self._value += 1
```

#### Pattern 6: Layer / Architectural Violation

**AST Markers:** An `ImportFrom` node found inside a module subtree that references a module identifier belonging to a forbidden architectural layer (e.g., routes importing data-layer modules, or library code importing transport frameworks).

**Detection Goal:** Stops code from bypassing architectural layers. Ensures UI/routes never talk directly to data storage without going through business logic.

**Covered by:** `layer-violation` (configurable per-package), `transport-in-library` (hardcoded framework list)

#### Pattern 7: Circular Dependency Graph

**AST Markers:** Multiple `ImportFrom` / `Import` root-level nodes that create a bidirectional loop between file modules when processed into a structural import graph (DFS cycle detection).

**Detection Goal:** Prevents tightly locked code files. File A importing File B, which imports File A, prevents clean separation and isolates your architecture.

**Covered by:** `import-cycle` (full-package DFS on import graph), `potential-circular-import` (per-file child→parent heuristic)

#### Pattern 8: Code Quality Gaps (AST-Based)

**AST Markers:** A variety of local syntactic patterns that indicate structural decay but were not in the original rule set. Added following a gap analysis against PyExamine, smellcheck, DPy, and code-quality-analyzer.

| Sub-pattern | Detector Rule | Severity | Threshold | What It Catches |
|-------------|--------------|----------|-----------|-----------------|
| Unreachable code after terminator | `dead-code` | warning | — | Statements after `return`/`raise`/`break`/`continue` in the same body |
| Long method/attribute chain | `message-chain` | info | depth > 3 | `a.b().c().d()` style chains |
| Stacked decorators | `excessive-decorators` | info | > 3 decorators | Functions or classes wrapped more than 3 times |
| Magic numeric literals | `magic-number` | info | — | Numeric literals other than 0, 1, -1 (`__init__` skipped) |
| Missing else/elif branch | `missing-else` | info | — | `if` blocks with 2+ statements but no `else`/`elif` |
| Class with <2 methods | `lazy-class` | info | — | Classes replaceable by a plain function or `@dataclass` |
| Deep inheritance hierarchy | `deep-inheritance` | warning | depth ≥ 4 | Effective inheritance chain depth exceeding 4 levels |

**Covered by:** `dead-code`, `message-chain`, `excessive-decorators`, `magic-number`, `missing-else`, `lazy-class`, `deep-inheritance`

## 4. Layer Violation Rules

Configurable per-package. Current configuration:

```python
LAYER_RULES = {
    "etl-demo": {
        "routes/": ["boti_data.", "boti.", "boti_dask."],  # routes must not import data/foundation
    },
    "etl-core": {
        "": ["fastapi", "starlette", "httpx"],  # library must not import transport
    },
}
```

## 5. Inline Suppression

```python
# spaghetti-ignore[rule1,rule2]: reason
# spaghetti-ignore:  (suppresses all rules on that line)
```

Applies to the flagged line and the line directly above it. Suppressed findings are counted in the report (not silently dropped).

## 6. Configuration

### CLI Flags

| Flag | Default | Description |
|------|---------|-------------|
| `--config` | — | YAML file with `packages: {name: path}` mapping |
| `--package NAME=PATH` | — | Add/override one package (repeatable) |
| `--packages` | all | Subset of registry to scan |
| `--severity` | `info` | Minimum severity to display |
| `--json` | off | JSON output instead of text |
| `--top` | 5 | Number of worst files to list |
| `--exclude` | — | Path substrings to exclude |
| `--min-duplicate-lines` | 5 | Minimum function length for duplication checks |
| `--twin-similarity` | 0.6 | Minimum similarity ratio for sync/async twin detection |
| `--plan` | off | Output a prioritized remediation plan instead of the standard report |

### YAML Config Format

```yaml
packages:
  my-lib: my-lib/src/my_lib
  my-service: services/my-service/src/my_service
```

## 7. Output Format

### Text Report (default)

1. **Summary header** — files/lines/functions scanned, issue counts
2. **Package Health Scorecard** — per-package grade, score, KLOC, issues
3. **Affected Files** — sorted by error count, then issue count
4. **Cross-File Findings** — duplication and import cycles
5. **Detailed Findings** — per-package, per-file breakdown
6. **Rule Summary** — table of rule name, total count, per-severity

### JSON Output (`--json`)

```json
{
  "issues": [
    {
      "file": "relative/path.py",
      "line": 42,
      "severity": "error",
      "rule": "import-cycle",
      "message": "...",
      "package": "my-lib"
    }
  ],
  "suppressed": 5
}
```

### Remediation Plan (`--plan`)

Each rule is scored by `severity_weight × fix_effort` (see §2.2 for severity weights, §10 for effort estimates). Issues are grouped by rule and ranked by descending score. Priority levels:

| Level | Score Range | Description |
|-------|-------------|-------------|
| P0 | ≥ 12.0 | CRITICAL — fix immediately (circular imports, god-classes) |
| P1 | ≥ 7.0 | HIGH — fix this sprint |
| P2 | ≥ 3.0 | MEDIUM — plan for next cycle |
| P3 | < 3.0 | LOW — track in backlog |

The plan output includes:
1. **Priority breakdown** — count of rules per priority level
2. **Ranked table** — rule name, severity, effort, issue count, score, affected files
3. **Recommended fix order** — grouped by priority with effort estimates

## 8. What the Detector Does NOT Check

- Test files (excluded by default via `--exclude tests/`)
- Generated code (`__pycache__`, `.pyc`)
- Docstring quality or coverage
- Naming conventions (use `ruff` for that)
- Security vulnerabilities (separate `tripwire` tool)
- Performance characteristics
- Runtime behavior or correctness

## 9. Extensibility

To add a new rule:
1. Implement a check function following the `def check_*(node, ...)` signature pattern
2. Register it in the appropriate list:
   - `ALL_CHECKS` — per-file AST checks (run once per file)
   - `SOURCE_CHECKS` — per-file source-text checks (run once per file, no AST)
   - `PACKAGE_CHECKS` — per-package checks needing all files' trees at once
   - For checks requiring custom parameters (e.g., similarity thresholds), call directly from `scan_package` (see `check_duplicate_functions_pkg`, `check_sync_async_twins_pkg`)
3. Add the rule name to the `Issue.rule` documentation
4. Add an effort estimate to `_FIX_EFFORT` (1.0 = trivial, 5.0 = major refactoring)
5. The scoring engine picks up new issues automatically via severity weights

## 10. Remediation Effort Estimates

The `--plan` mode uses effort estimates (1–5 scale) combined with severity weights to prioritize fixes:

| Effort | Scale | Example Rules |
|--------|-------|---------------|
| trivial (0.5) | Delete a line, add a type hint | `unused-import`, `dead-code`, `magic-number`, `missing-param-type` |
| minor (1.0) | Add a constant, rename, small refactor | `missing-return-type`, `bare-except`, `encapsulation-violation`, `message-chain`, `excessive-decorators` |
| moderate (1.5–3.0) | Extract method, restructure params | `long-function`, `high-complexity`, `too-many-params`, `sync-async-duplication`, `duplicate-function-body` |
| major (4.0–5.0) | Split class, break circular deps | `god-class`, `import-cycle`, `layer-violation`, `deep-inheritance` |

Priority score = `severity_weight × effort`. Higher scores are fixed first — this ensures structural issues (high-severity, high-effort) are tackled before cosmetic ones (low-severity, low-effort).
