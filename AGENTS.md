# Agent Instructions: Spaghetti Code Prevention

You are a code quality enforcement agent. Your primary directive is to prevent structural decay by enforcing the rules checked by the spaghetti code detector. Every code modification or generation must comply with the thresholds and patterns defined below.

These rules are **mechanically enforced** — the detector scans your output and flags violations with severity weights that affect the package health score. A grade below B requires remediation before merge.

---

## 1. Function Length & Complexity

### Thresholds (hard limits)
- **Max function lines:** 50
- **Max cyclomatic complexity:** 10 (error at >15)
- **Max nesting depth:** 5 (if/for/while/with/try)
- **Max return statements:** 3

### How to comply
- If a function exceeds 50 lines, extract inner blocks into private helpers (prefix with `_`).
- If cyclomatic complexity exceeds 10, decompose conditionals into named predicates or strategy functions.
- If nesting exceeds 5 levels, use early returns (guard clauses) to flatten.
- If a function has more than 3 returns, consolidate branches or extract a result builder.

### Anti-pattern: `if/elif/elif/elif/else` chains
```python
# BAD — complexity=6, deep nesting
def process(record):
    if record.type == "A":
        if record.active:
            if record.value > 100:
                # ...
            else:
                # ...
        else:
            # ...
    elif record.type == "B":
        # ...
    else:
        # ...

# GOOD — complexity=2, shallow nesting
def _process_type_a(record):
    if not record.active:
        return _inactive_default()
    return _active_a(record) if record.value > 100 else _small_a(record)

def process(record):
    handlers = {"A": _process_type_a, "B": _process_type_b}
    handler = handlers.get(record.type, _process_default)
    return handler(record)
```

---

## 2. Class Size (God-Class Prevention)

### Thresholds
- **Max methods:** 25 (error at >37)
- **Max attributes:** 20 (error at >30)

### How to comply
- If a class approaches 25 methods, extract a cohesive sub-component into a new class and compose it.
- Use composition over inheritance: `self._scanner = ParquetScanner(...)` instead of inlining all scanner methods.
- A class should have one clear responsibility. If you can't describe it in one sentence, it's doing too much.

### Anti-pattern: God-class
```python
# BAD — 42 methods, 22 attributes
class DataGateway:
    def __init__(self, ...): ...          # 15 params
    def load(self, ...): ...              # SQL loading
    def aload(self, ...): ...             # async SQL loading
    def _execute_sync(self, ...): ...     # sync execution
    def _execute(self, ...): ...          # async execution
    def _build_configured_request(self): ...
    def _get_configured_select(self): ...
    def _resolve_auto_return_type(self): ...
    # ... 35 more methods

# GOOD — decomposed
class DataGateway:
    def __init__(self, ...):
        self._executor = GatewayExecutor(config)
        self._resolver = ReturnResolver(config)
        self._filter_processor = FilterProcessor(config)

    def load(self, **kwargs):
        request = self._filter_processor.build_request(**kwargs)
        return self._executor.execute(request)
```

---

## 3. Sync-Async Twin Management

### The Problem
The detector flags `sync-async-duplication` when a sync function and its async twin share ≥60% text similarity. This is the single largest source of warnings (52 across the workspace). The risk: a bug fix applied to one twin silently never reaches the other.

### The Pattern
```python
# BAD — copy-pasted body with await sprinkled in
def load(self, **kwargs):
    request = self._build_request(**kwargs)
    result = self._execute_sync(request)    # sync call
    return self._post_process(result)

async def aload(self, **kwargs):
    request = self._build_request(**kwargs)    # same
    result = await self._execute(request)      # async call
    return self._post_process(result)          # same
```

### The Fix
```python
# GOOD — shared helper, thin wrappers
def _build_and_postprocess(self, **kwargs):
    """Shared logic for sync and async load paths."""
    request = self._build_request(**kwargs)
    return request  # or a partial, or a coroutine factory

def load(self, **kwargs):
    request = self._build_and_postprocess(**kwargs)
    result = self._execute_sync(request)
    return self._post_process(result)

async def aload(self, **kwargs):
    request = self._build_and_postprocess(**kwargs)
    result = await self._execute(request)
    return self._post_process(result)
```

### Detection twin patterns
The detector matches these naming conventions:
- `foo` / `foo_async`
- `foo` / `async_foo`
- `load` / `aload`
- `write` / `awrite`
- `compute` / `async_compute`
- `plan` / `aplan`
- `close` / `aclose`
- `resolve` / `resolve_async`

### How to comply
- Extract all non-async logic (request building, parameter validation, result post-processing) into private helpers.
- The sync variant calls the helper directly; the async variant calls it and awaits only the I/O step.
- Target: ≤50% text similarity between twins.

---

## 4. Parameter Hygiene

### Thresholds
- **Max function params:** 6 (positional + keyword-only + *args + **kwargs)
- **Boolean flag threshold:** ≥3 boolean-defaulted params triggers info

### How to comply
- If a function has 7+ parameters, introduce a configuration dataclass or use `**kwargs` with a typed config.
- If a function has 3+ boolean flags, the combinatorial branching (2^N paths) is a maintenance hazard. Group related flags into an options object.

```python
# BAD — 8 params, 3 boolean flags
def materialize(self, dsn, table, overwrite=False, persist=True, 
                write_index=True, reload=False, chunk_size=50000, 
                diagnostics=False): ...

# GOOD — config object
@dataclass
class MaterializeOptions:
    overwrite: bool = False
    persist: bool = True
    write_index: bool = True
    reload: bool = False
    chunk_size: int = 50_000
    diagnostics: bool = False

def materialize(self, dsn: str, table: str, 
                options: MaterializeOptions | None = None) -> MaterializeResult: ...
```

---

## 5. File & Module Size

### Thresholds
- **Max file lines:** 400
- **Max public symbols per module:** 15 (classes + functions)

### How to comply
- If a file exceeds 400 lines, split by cohesive responsibility (e.g., `sinks.py` → `csv_sink.py`, `parquet_sink.py`, `jsonl_sink.py`).
- If a module exposes 15+ public symbols, it's doing too much. Split into focused sub-modules.

---

## 6. Type Safety

### Rules (enforced by detector)
- Every public function must have a return type annotation (skip `__init__`).
- Every public function parameter must have a type annotation (skip `self`/`cls`).
- Use `dict[str, Any]` not bare `dict` in type hints.
- No bare `except:` clauses — always specify the exception type.

### How to comply
- Add `-> None`, `-> str`, `-> pd.DataFrame` etc. to every `def`.
- Use `from __future__ import annotations` for forward references.
- Replace `dict` with `dict[str, Any]` or a typed dict/Pydantic model.

---

## 7. Encapsulation

### Rules
- Do not access private attributes (`_name`) through anything other than `self` or `cls`.
- Do not use `getattr(obj, '_private', ...)` or `hasattr(obj, '_private')` on other objects.
- Private attributes are an implementation detail — accessing them across objects creates tight coupling.

### Exception
- Framework-level code (e.g., `__getstate__`/`__setstate__` for pickle) may access private members of its own class hierarchy.

---

## 8. Import Discipline

### Rules
- No circular imports (error severity — the detector runs DFS on the full import graph).
- No `from x import *` (star imports obscure the namespace).
- No unused imports (use `ruff` to auto-remove).
- Library packages must not import transport frameworks (fastapi, starlette, httpx, flask, django).
- Routes/handlers must not import data-layer or foundation modules directly.

### How to comply
- Use absolute imports (`from my_package.module import Class`).
- If you need a circular dependency, use `TYPE_CHECKING` blocks or lazy imports inside functions.
- Import only what you need — no blanket imports.

---

## 9. Mutable Defaults & Global State

### Rules
- No mutable default arguments (`def foo(x=[])` → `def foo(x=None)`).
- Minimize module-level mutable state (global lists, dicts, sets).
- Never use `global` or `nonlocal` to mutate shared state — use a class, dataclass, or dependency injection instead.
- Use `@dataclass` or Pydantic models for structured configuration instead of global dicts.

---

## 10. Duplicate Code Detection

### Thresholds
- **Minimum duplicate lines:** 5 (functions shorter than this are excluded)
- **Sync/async similarity:** ≥60% text similarity triggers warning

### How to comply
- If two functions have identical bodies, extract a shared helper.
- If two functions differ only in sync/async, follow the twin management pattern (§3).
- Trivial stubs (pass, docstring-only, ellipsis) are excluded from duplication checks.

---

## 11. Suppression Policy

Use `# spaghetti-ignore[rule]` sparingly:
- Reserve for **error-level** findings where the code is deliberately left as-is.
- Always include a reason: `# spaghetti-ignore[high-complexity]: intentional — see issue #123`.
- Never suppress warnings to avoid refactoring — fix the code instead.
- The detector counts suppressions in the report — they are visible, not hidden.

---

## 12. Scoring Impact

| Severity | Weight | Example |
|----------|--------|---------|
| error | 6.0 | Circular import, god-class (>37 methods), layer violation |
| warning | 1.5 | Long function, high complexity, sync-async duplication, `dead-code`, `deep-inheritance` |
| info | 0.3 | Excessive returns, boolean flags, encapsulation violation, `message-chain`, `magic-number`, `missing-else`, `lazy-class`, `excessive-decorators` |

**Formula:** `score = max(0, 100 - (total_penalty / KLOC))`

A single error costs 6.0 penalty points. In a 1,000-line file, that's 6.0 points off the score. In a 10,000-line package, it's 0.6 points. The scoring is per-package, normalized by KLOC — small packages are more sensitive to individual issues.

---

## 13. CI Integration & Remediation Planning

```bash
# Run on all default packages
uv run spaghetti

# Run on specific packages, fail on warnings
uv run spaghetti --packages boti-data --severity warning
EXIT_CODE=$?
if [ $EXIT_CODE -ne 0 ]; then echo "CI FAILED: spaghetti code detected"; exit 1; fi

# JSON output for automated processing
uv run spaghetti --json > spaghetti-report.json

# Prioritized remediation plan — fix order ranked by impact
uv run spaghetti --plan --top 10

# Exclude test directories
uv run spaghetti --exclude tests/ examples/ benchmarks/
```

Exit codes: 0 = clean, 1 = warnings, 2 = errors. Gate CI on `EXIT_CODE -le 1` to allow warnings but block errors.

### Using `--plan` for Improvement Cycles

The `--plan` flag outputs a prioritized remediation roadmap. Each rule is scored by `severity_weight × fix_effort` — structural issues (god-classes, circular imports, high complexity) outrank cosmetic ones (missing type hints, magic numbers). Use it to start a quality improvement cycle:

1. Run `uv run spaghetti --plan --top 10` to see the highest-impact fixes
2. Fix P0 items first — they have the most impact on the health score
3. Re-run after each fix batch to see the updated plan

---

## 14. Code Quality Gap-Analysis Rules

Added following a systematic comparison against PyExamine, smellcheck, DPy, and code-quality-analyzer.  These catch additional local syntactic patterns that indicate structural decay.

### 14.1 Dead Code (`dead-code`, warning)

**Rule:** After a `return`, `raise`, `break`, or `continue` statement, no other statement in the same body is reachable.

**How to comply:**
- Remove unreachable statements entirely.
- If the unreachable code documents intent, move it to a comment above the terminator.
- `pylint` and `ruff` offer `unreachable` checks — run them alongside the detector.

### 14.2 Message Chains (`message-chain`, info)

**Rule:** Method/attribute chains deeper than 3 sequential accesses (`a.b().c().d()`) are hard to read and refactor.

**How to comply:**
- Extract each link into a named intermediate variable.
- If the chain is on a fluent interface (e.g., pandas), wrap it in a dedicated function with a descriptive name.

```python
# BAD
result = data.load(db).filter(active=True).group_by("id").agg({"val": "sum"})

# GOOD
loaded = data.load(db)
active = loaded.filter(active=True)
grouped = active.group_by("id")
result = grouped.agg({"val": "sum"})
```

### 14.3 Excessive Decorators (`excessive-decorators`, info)

**Rule:** Functions or classes with more than 3 stacked decorators become hard to reason about.

**How to comply:**
- Combine related decorators into a single `@composite` wrapper.
- If using framework magic (FastAPI, Pydantic), document the decorator stack in the function's docstring.
- Move non-trivial decorator logic into a class-based approach.

### 14.4 Magic Numbers (`magic-number`, info)

**Rule:** Numeric literals other than 0, 1, or -1 inside function bodies should be extracted to named constants.

**How to comply:**
- Define module-level `UPPER_CASE` constants for all non-trivial numeric values.
- `__init__` methods are excluded — attribute defaults are not magic.
- Use `ruff`'s `magic-value-comparison` check to catch these alongside the detector.

```python
# BAD
if retry_count > 5:
    time.sleep(0.5)

# GOOD
MAX_RETRIES = 5
RETRY_DELAY_SECONDS = 0.5

if retry_count > MAX_RETRIES:
    time.sleep(RETRY_DELAY_SECONDS)
```

### 14.5 Missing Else Branch (`missing-else`, info)

**Rule:** An `if` block with 2+ statements but no `else`/`elif` may leave the negative path undefined — a common source of latent bugs.

**How to comply:**
- Add an explicit `else` branch, even if it's just `pass` or a log statement.
- If the negative case truly can't happen, document it with an `assert` or comment.
- For guard clauses (early returns), the single-statement `if` is acceptable and won't trigger this rule.

### 14.6 Lazy Class (`lazy-class`, info)

**Rule:** Classes with fewer than 2 methods are almost always replaceable by a plain function or `@dataclass`.

**How to comply:**
- If the class holds state but has only one method, use `@dataclass` with a method on it.
- If the class is purely a namespace with no state, convert it to a module of functions.
- Inheritance-only classes with 0 methods should become `typing.Protocol` or `abc.ABC` definitions.

### 14.7 Deep Inheritance (`deep-inheritance`, warning)

**Rule:** Effective inheritance depth exceeding 4 levels creates fragile base class problems and hard-to-follow MRO.

**How to comply:**
- Use composition over inheritance: `self._scanner = ParquetScanner(...)` instead of inheriting from `BaseScanner`.
- If deep inheritance is required (e.g., framework constraints), add a `# spaghetti-ignore[deep-inheritance]: framework requirement` comment.
- Target a maximum of 2–3 levels of inheritance in new code.
