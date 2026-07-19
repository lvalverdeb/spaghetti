# Proposal: fix `missing-else`'s false positives on guard clauses

**Status:** proposed, not yet implemented
**Affects:** `spaghetti` (Python, spec-of-record) and `spaghetti-rs` (Rust mirror) — both implement this rule identically
**Trigger:** a real-world pass through all 26 `missing-else` findings in `boti/` (2026-07) turned up zero genuine bugs — every hit was a false positive against idiomatic guard-clause style already used consistently across the codebase.

## 1. Current behavior

Both implementations flag any `if` block with no `else`/`elif` and 2+ statements in its body. The logic is identical in both languages and does not look at what those statements *are*:

**Python** — `spaghetti/src/spaghetti/checks/ast_per_file.py:925-947`
```python
def check_missing_else(tree: ast.Module, filepath: Path, pkg: str) -> list[Issue]:
    """Flag ``if`` blocks with 2+ statements but no ``else``/``elif``."""
    issues: list[Issue] = []
    _NON_TRIVIAL_BODY_THRESHOLD = 2

    for node in ast.walk(tree):
        if not isinstance(node, ast.If):
            continue
        if node.orelse:
            continue
        if len(node.body) >= _NON_TRIVIAL_BODY_THRESHOLD:
            issues.append(Issue(..., rule="missing-else", ...))
    return issues
```

**Rust** — `spaghetti-rs/src/checks/ast_per_file.rs:407-433`
```rust
impl<'a> Visitor for MissingElseVisitor<'a> {
    fn visit_stmt_if(&mut self, node: rustpython_ast::StmtIf) {
        if node.orelse.is_empty() && node.body.len() >= NON_TRIVIAL_BODY_THRESHOLD {
            self.issues.push(issue(..., "missing-else", ...));
        }
        self.generic_visit_stmt_if(node);
    }
}
```

Existing tests only cover the trivial cases (`spaghetti/tests/test_detector.py:1530-1556`): flags a bare 2-statement `if`, and allows `if/else`, `if/elif`, and single-statement `if`. Nothing exercises the guard-clause shape, which is exactly what makes up the entire false-positive population found in practice.

## 2. Evidence: 26/26 real hits were false positives

Auditing every `missing-else` finding across 12 files in `boti/` (`agent.py`, `filesystem_options.py`, `lifecycle.py`, `logger.py`, `logger_filters.py`, `logger_runtime.py`, `managed_resource.py`, `pickle_security.py`, `project.py`, `secure_io.py`, `main.py`) found that in **every case** the `if` body's last statement was one of `return`, `raise`, `continue`, or `break` — meaning control flow already terminates before the "missing" negative path could matter. They fall into five recurring shapes:

1. **Guard clause + early return** — negative path is simply "the rest of the function":
   ```python
   if self._skip_logger:
       self.logger = None
       return
   ```
2. **Guard-then-raise** — no continuation is possible after a `raise`:
   ```python
   elif config_overrides:
       unexpected_keys = ", ".join(sorted(config_overrides))
       raise TypeError(f"Unexpected config override(s)...: {unexpected_keys}")
   ```
3. **"Set only if absent" idempotent setup** — negative path is "leave it alone":
   ```python
   if "timeout" not in options:
       timeout = config.fs_read_timeout or config.fs_connect_timeout
       if timeout is not None:
           options["timeout"] = timeout
   ```
4. **Type-dispatch chain, each branch returns** — falling through to the next `isinstance` check *is* the intended negative path:
   ```python
   if isinstance(value, dict):
       ...
       return redacted
   if isinstance(value, list):
       ...
       return redacted_list
   ```
5. **Skip-and-continue inside a loop** — negative path is "next iteration":
   ```python
   if candidate not in seen:
       seen.add(candidate)
       yield candidate
   ```

In each shape, adding an `else:` would only add a nesting level around code that already reads correctly — it actively fights the guard-clause style the rest of the codebase (including code written in the same session that fixed these files) already uses on purpose.

## 3. Proposed fix

Skip the flag when the `if` body's **last statement** is one that already terminates the block's control flow: `return`, `raise`, `continue`, or `break`. This directly targets shapes 1, 2, 4, and 5 above (anywhere the "positive path" doesn't fall through into the rest of the enclosing block, there is no meaningful "negative path" left to add).

Shape 3 ("set only if absent") is different — it doesn't end in a terminator, it just runs out of statements. That's arguably still a case where "the if body's last statement" heuristic doesn't apply, but it's also the shape where an explicit `else: pass` would be genuinely pointless noise, not a hidden bug. Rather than special-case it now, ship the terminator fix first and re-measure: shape 3 may turn out rare enough in practice (it was ~7 of 26 in this sample, all in option-builder/idempotent-setup code, a recognizable minority) that it's fine to leave as-is, or it can be addressed later as a distinct "trailing conditional assignment" allowance if it keeps showing up as noise.

### 3.1 Python change

`spaghetti/src/spaghetti/checks/ast_per_file.py`, in `check_missing_else`:

```python
_TERMINAL_STMT_TYPES = (ast.Return, ast.Raise, ast.Continue, ast.Break)

def check_missing_else(tree: ast.Module, filepath: Path, pkg: str) -> list[Issue]:
    """Flag ``if`` blocks with 2+ statements but no ``else``/``elif``.

    Skipped when the ``if`` body's last statement already terminates control
    flow (``return``/``raise``/``continue``/``break``): the negative path is
    either "the rest of the function" or "the next loop iteration", and is
    not missing.
    """
    issues: list[Issue] = []
    _NON_TRIVIAL_BODY_THRESHOLD = 2

    for node in ast.walk(tree):
        if not isinstance(node, ast.If):
            continue
        if node.orelse:
            continue
        if len(node.body) < _NON_TRIVIAL_BODY_THRESHOLD:
            continue
        if isinstance(node.body[-1], _TERMINAL_STMT_TYPES):
            continue
        issues.append(Issue(..., rule="missing-else", ...))
    return issues
```

### 3.2 Rust change

`spaghetti-rs/src/checks/ast_per_file.rs`, in `MissingElseVisitor::visit_stmt_if`:

```rust
fn is_terminal_stmt(stmt: &Stmt) -> bool {
    matches!(
        stmt,
        Stmt::Return(_) | Stmt::Raise(_) | Stmt::Continue(_) | Stmt::Break(_)
    )
}

impl<'a> Visitor for MissingElseVisitor<'a> {
    fn visit_stmt_if(&mut self, node: rustpython_ast::StmtIf) {
        let is_terminated = node.body.last().is_some_and(is_terminal_stmt);
        if node.orelse.is_empty() && node.body.len() >= NON_TRIVIAL_BODY_THRESHOLD && !is_terminated
        {
            self.issues.push(issue(..., "missing-else", ...));
        }
        self.generic_visit_stmt_if(node);
    }
}
```

Both changes are a pure narrowing of an existing rule (fewer findings, never more), so no config/CLI/schema changes are needed on either side.

## 4. Test additions

Following the existing four-case pattern at `spaghetti/tests/test_detector.py:1530-1556` and its (currently nonexistent) Rust counterpart, add one case per terminal-statement kind, plus the "still flags a genuine non-terminated 2+-statement if" regression to prove the narrowing doesn't over-suppress:

```python
def test_check_missing_else_allows_guard_clause_return():
    source = "def f():\n    if x:\n        a = 1\n        return\n    return a\n"
    assert ds.check_missing_else(_parse(source), Path("f.py"), "pkg") == []


def test_check_missing_else_allows_guard_clause_raise():
    source = "def f():\n    if x:\n        a = 1\n        raise ValueError(a)\n"
    assert ds.check_missing_else(_parse(source), Path("f.py"), "pkg") == []


def test_check_missing_else_allows_loop_skip_continue():
    source = "def f():\n    for x in y:\n        if x in seen:\n            a = 1\n            continue\n"
    assert ds.check_missing_else(_parse(source), Path("f.py"), "pkg") == []


def test_check_missing_else_allows_loop_skip_break():
    source = "def f():\n    for x in y:\n        if x is done:\n            a = 1\n            break\n"
    assert ds.check_missing_else(_parse(source), Path("f.py"), "pkg") == []


def test_check_missing_else_still_flags_non_terminated_if():
    # Sanity check: the narrowing must not swallow genuine hits — a 2+
    # statement if with no else/elif/terminator is still flagged.
    source = "def f():\n    if x:\n        a = 1\n        b = 2\n    return a + b\n"
    issues = ds.check_missing_else(_parse(source), Path("f.py"), "pkg")
    assert len(issues) == 1
```

Mirror the same five cases as Rust integration/unit tests for `check_missing_else` in `spaghetti-rs`.

## 5. Verification plan

1. Add and pass the new unit tests in both languages.
2. Re-run the existing four `missing-else` tests in both languages unchanged — they must still pass (none of them involve a terminator, so the narrowing doesn't affect them).
3. Real-code conformance check (the methodology used throughout the Rust port): run both implementations over `boti/`, `boti-data/`, `boti-dask/`, `tripwire/`, and `spaghetti`/`spaghetti-rs`'s own source, and confirm:
   - the `missing-else` finding set is identical between Python and Rust (as it was before), just smaller;
   - the `boti/` sample specifically drops from 26 to (expected) 0, given every hit in that audit was terminator-shaped.
4. Bump both packages' patch version (same pattern as the `--package` path-validation fix) once merged.

## 6. Out of scope

- Shape 3 ("set only if absent") is deliberately left alone for now — see §3 rationale.
- No change to `missing-else`'s severity, message text, or its counting toward the score/grade — this is a precision fix, not a policy change.
- `SDD.md`'s rule catalog entries for `missing-else` (lines 89 and 213) don't need wording changes; the behavior they describe ("if blocks with 2+ statements but no else/elif") is still accurate, just now correctly scoped to blocks where that's actually missing.
