"""Per-file AST-based checks — the core 27 rules."""

from __future__ import annotations

import ast
from collections.abc import Callable
from pathlib import Path

from spaghetti.ast_helpers import (
    _count_own_returns,
    _cyclomatic_complexity,
    _dump_stmts,
    _has_return_type_hint,
    _is_private,
    _line_count,
    _nesting_depth,
    _param_has_type_hint,
    _walk_with_class_context,
)
from spaghetti.config import (
    COMPLEXITY_THRESHOLD,
    LAYER_RULES,
    MAX_CLASS_ATTRS,
    MAX_CLASS_METHODS,
    MAX_DECORATORS,
    MAX_FUNC_PARAMS,
    MAX_FUNCTION_LINES,
    MAX_INHERITANCE_DEPTH,
    MAX_MESSAGE_CHAIN_DEPTH,
    MAX_NESTING_DEPTH,
    MAX_RETURNS,
    MIN_BOOLEAN_FLAGS,
)
from spaghetti.config import DUNDER_RE as _DUNDER_RE
from spaghetti.models import Issue

__all__ = ["ALL_CHECKS"]

# Type aliases for check function signatures
FileCheck = Callable[[ast.Module, Path, str], list[Issue]]


# ── Rule: Long Functions ──────────────────────────────────────────────────────


def check_long_functions(tree: ast.Module, filepath: Path, pkg: str) -> list[Issue]:
    issues: list[Issue] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            lines = _line_count(node)
            if lines > MAX_FUNCTION_LINES:
                issues.append(
                    Issue(
                        file=filepath,
                        line=node.lineno,
                        severity="warning",
                        rule="long-function",
                        package=pkg,
                        message=f"{node.name}() is {lines} lines (max {MAX_FUNCTION_LINES})",
                    )
                )
    return issues


# ── Rule: High Cyclomatic Complexity ──────────────────────────────────────────


def check_complexity(tree: ast.Module, filepath: Path, pkg: str) -> list[Issue]:
    issues: list[Issue] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            cc = _cyclomatic_complexity(node)
            if cc > COMPLEXITY_THRESHOLD:
                issues.append(
                    Issue(
                        file=filepath,
                        line=node.lineno,
                        severity="error" if cc > 15 else "warning",
                        rule="high-complexity",
                        package=pkg,
                        message=f"{node.name}() has complexity {cc} (max {COMPLEXITY_THRESHOLD})",
                    )
                )
    return issues


# ── Rule: Missing Type Hints ──────────────────────────────────────────────────


def check_missing_types(tree: ast.Module, filepath: Path, pkg: str) -> list[Issue]:
    issues: list[Issue] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if _is_private(node.name):
                continue
            if not _has_return_type_hint(node) and node.name != "__init__":
                issues.append(
                    Issue(
                        file=filepath,
                        line=node.lineno,
                        severity="warning",
                        rule="missing-return-type",
                        package=pkg,
                        message=f"{node.name}() missing return type annotation",
                    )
                )
            args = node.args.args
            for arg in args:
                if arg.arg in ("self", "cls"):
                    continue
                if not _param_has_type_hint(arg):
                    issues.append(
                        Issue(
                            file=filepath,
                            line=node.lineno,
                            severity="info",
                            rule="missing-param-type",
                            package=pkg,
                            message=f"{node.name}(): param '{arg.arg}' missing type annotation",
                        )
                    )
    return issues


# ── Rule: Excessive Parameters ────────────────────────────────────────────────


def check_excessive_params(tree: ast.Module, filepath: Path, pkg: str) -> list[Issue]:
    issues: list[Issue] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            total = len(node.args.args) + len(node.args.kwonlyargs)
            if node.args.vararg:
                total += 1
            if node.args.kwarg:
                total += 1
            if total > MAX_FUNC_PARAMS:
                issues.append(
                    Issue(
                        file=filepath,
                        line=node.lineno,
                        severity="warning",
                        rule="too-many-params",
                        package=pkg,
                        message=f"{node.name}() has {total} params (max {MAX_FUNC_PARAMS})",
                    )
                )
    return issues


# ── Rule: Excessive Return Points ─────────────────────────────────────────────


def check_excessive_returns(tree: ast.Module, filepath: Path, pkg: str) -> list[Issue]:
    """Many exit points in one function make control flow harder to trace."""
    issues: list[Issue] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            n_returns = _count_own_returns(node)
            if n_returns > MAX_RETURNS:
                issues.append(
                    Issue(
                        file=filepath,
                        line=node.lineno,
                        severity="info",
                        rule="excessive-returns",
                        package=pkg,
                        message=f"{node.name}() has {n_returns} return statements (max {MAX_RETURNS})",
                    )
                )
    return issues


# ── Rule: Boolean Flag Parameters ─────────────────────────────────────────────


def check_boolean_flag_params(tree: ast.Module, filepath: Path, pkg: str) -> list[Issue]:
    """Several boolean-defaulted params on one function multiply the number of
    behaviors it has to support (2^N combinations)."""
    issues: list[Issue] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            flags: list[str] = []
            pos_args = node.args.args
            defaults = node.args.defaults
            paired = (
                list(zip(pos_args[len(pos_args) - len(defaults) :], defaults)) if defaults else []
            )
            for arg, default in paired:
                if isinstance(default, ast.Constant) and isinstance(default.value, bool):
                    flags.append(arg.arg)
            for arg, default in zip(node.args.kwonlyargs, node.args.kw_defaults):
                if (
                    default is not None
                    and isinstance(default, ast.Constant)
                    and isinstance(default.value, bool)
                ):
                    flags.append(arg.arg)
            if len(flags) >= MIN_BOOLEAN_FLAGS:
                issues.append(
                    Issue(
                        file=filepath,
                        line=node.lineno,
                        severity="info",
                        rule="boolean-flag-params",
                        package=pkg,
                        message=(
                            f"{node.name}() has {len(flags)} boolean flag params "
                            f"({', '.join(flags)}) — combinations multiply branching"
                        ),
                    )
                )
    return issues


# ── Rule: Deep Nesting ────────────────────────────────────────────────────────


def check_deep_nesting(tree: ast.Module, filepath: Path, pkg: str) -> list[Issue]:
    issues: list[Issue] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            depth = _nesting_depth(node)
            if depth > MAX_NESTING_DEPTH:
                issues.append(
                    Issue(
                        file=filepath,
                        line=node.lineno,
                        severity="warning",
                        rule="deep-nesting",
                        package=pkg,
                        message=f"{node.name}() has nesting depth {depth} (max {MAX_NESTING_DEPTH})",
                    )
                )
    return issues


# ── Rule: Untyped Dict Usage ──────────────────────────────────────────────────


def check_untyped_dicts(tree: ast.Module, filepath: Path, pkg: str) -> list[Issue]:
    """Find bare `dict` without type parameters in annotations via AST inspection."""
    issues: list[Issue] = []

    def get_bare_dict_lines(annotation_node: ast.AST | None) -> list[int]:
        if not annotation_node:
            return []

        lines: list[int] = []

        def visit(n: ast.AST) -> None:
            if isinstance(n, ast.Subscript):
                if isinstance(n.value, ast.Name) and n.value.id == "dict":
                    visit(n.slice)
                    return
            elif isinstance(n, ast.Name) and n.id == "dict":
                lines.append(n.lineno)

            for child in ast.iter_child_nodes(n):
                visit(child)

        visit(annotation_node)
        return lines

    for node in ast.walk(tree):
        annotations_to_check: list[ast.AST] = []

        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.returns:
                annotations_to_check.append(node.returns)
        elif isinstance(node, ast.arg):
            if node.annotation:
                annotations_to_check.append(node.annotation)
        elif isinstance(node, ast.AnnAssign):
            if node.annotation:
                annotations_to_check.append(node.annotation)
        elif isinstance(node, getattr(ast, "TypeAlias", type(None))):
            if hasattr(node, "value"):
                annotations_to_check.append(node.value)

        for ann in annotations_to_check:
            for line in get_bare_dict_lines(ann):
                issues.append(
                    Issue(
                        file=filepath,
                        line=line,
                        severity="info",
                        rule="untyped-dict",
                        package=pkg,
                        message="Bare 'dict' used in type hint — use dict[str, Any] or similar",
                    )
                )

    unique_issues = {(i.line, i.rule): i for i in issues}
    return list(unique_issues.values())


# ── Rule: Unused Imports ──────────────────────────────────────────────────────


def check_unused_imports(tree: ast.Module, filepath: Path, pkg: str) -> list[Issue]:
    """Flags imported names never referenced in the file.
    ``__init__.py`` is skipped (re-exports)."""
    if filepath.name == "__init__.py":
        return []

    imported: dict[str, int] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                name = (alias.asname or alias.name).split(".")[0]
                if name != "_":
                    imported[name] = node.lineno
        elif isinstance(node, ast.ImportFrom):
            if node.module == "__future__":
                continue
            for alias in node.names:
                if alias.name == "*":
                    continue
                name = alias.asname or alias.name
                if name != "_":
                    imported[name] = node.lineno

    if not imported:
        return []

    used: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            used.add(node.id)
        elif (
            isinstance(node, ast.Assign)
            and any(isinstance(t, ast.Name) and t.id == "__all__" for t in node.targets)
            and isinstance(node.value, (ast.List, ast.Tuple))
        ):
            for elt in node.value.elts:
                if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
                    used.add(elt.value)

    issues: list[Issue] = []
    for name, lineno in sorted(imported.items(), key=lambda kv: kv[1]):
        if name not in used:
            issues.append(
                Issue(
                    file=filepath,
                    line=lineno,
                    severity="warning",
                    rule="unused-import",
                    package=pkg,
                    message=f"'{name}' imported but never used",
                )
            )
    return issues


# ── Rule: Swallowed Exceptions ────────────────────────────────────────────────


def check_swallowed_exceptions(tree: ast.Module, filepath: Path, pkg: str) -> list[Issue]:
    """``except ...: pass`` (or ``...``) silently discards failures."""
    issues: list[Issue] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.ExceptHandler):
            continue
        body = node.body
        is_pass_only = len(body) == 1 and isinstance(body[0], ast.Pass)
        is_ellipsis_only = (
            len(body) == 1
            and isinstance(body[0], ast.Expr)
            and isinstance(body[0].value, ast.Constant)
            and body[0].value.value is Ellipsis
        )
        if is_pass_only or is_ellipsis_only:
            exc_name = (
                getattr(node.type, "id", None)
                or (node.type and ast.dump(node.type, annotate_fields=False))
                or "Exception"
            )
            issues.append(
                Issue(
                    file=filepath,
                    line=node.lineno,
                    severity="warning",
                    rule="swallowed-exception",
                    package=pkg,
                    message=f"except {exc_name}: silently discards the error with no log/reraise",
                )
            )
    return issues


# ── Rule: Duplicate If/Else Branches ──────────────────────────────────────────


def check_duplicate_branches(tree: ast.Module, filepath: Path, pkg: str) -> list[Issue]:
    """An if/else whose two branches are structurally identical means the
    condition has no effect."""
    issues: list[Issue] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.If) or not node.orelse:
            continue
        if len(node.orelse) == 1 and isinstance(node.orelse[0], ast.If):
            continue
        if not node.body:
            continue
        if _dump_stmts(node.body) == _dump_stmts(node.orelse):
            issues.append(
                Issue(
                    file=filepath,
                    line=node.lineno,
                    severity="warning",
                    rule="duplicate-branch",
                    package=pkg,
                    message="if/else branches are structurally identical — the condition has no effect",
                )
            )
    return issues


# ── Rule: Encapsulation Violations ────────────────────────────────────────────


def check_encapsulation_violations(tree: ast.Module, filepath: Path, pkg: str) -> list[Issue]:
    """Reaching into another object's private (``_name``) attribute from outside
    self/cls/its own class."""
    issues: list[Issue] = []

    def is_allowed_base(base: ast.AST, class_name: str | None) -> bool:
        if isinstance(base, ast.Name) and base.id in ("self", "cls", class_name):
            return True
        if (
            isinstance(base, ast.Call)
            and isinstance(base.func, ast.Name)
            and base.func.id == "super"
        ):
            return True
        return False

    for node, class_name in _walk_with_class_context(tree):
        if (
            isinstance(node, ast.Attribute)
            and isinstance(node.ctx, ast.Load)
            and node.attr.startswith("_")
            and not _DUNDER_RE.match(node.attr)
            and not is_allowed_base(node.value, class_name)
        ):
            issues.append(
                Issue(
                    file=filepath,
                    line=node.lineno,
                    severity="info",
                    rule="encapsulation-violation",
                    package=pkg,
                    message=f"Accesses private member '.{node.attr}' through something other than self/cls",
                )
            )
        elif (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id in ("getattr", "setattr", "hasattr")
            and len(node.args) >= 2
            and isinstance(node.args[1], ast.Constant)
            and isinstance(node.args[1].value, str)
            and node.args[1].value.startswith("_")
            and not _DUNDER_RE.match(node.args[1].value)
            and not is_allowed_base(node.args[0], class_name)
        ):
            issues.append(
                Issue(
                    file=filepath,
                    line=node.lineno,
                    severity="info",
                    rule="encapsulation-violation",
                    package=pkg,
                    message=f"{node.func.id}(..., '{node.args[1].value}', ...) reaches into a private attribute",
                )
            )
    return issues


# ── Rule: God Class ───────────────────────────────────────────────────────────


def check_god_class(tree: ast.Module, filepath: Path, pkg: str) -> list[Issue]:
    """A class with too many methods or instance attributes."""
    issues: list[Issue] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue
        methods = [n for n in node.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
        attrs: set[str] = set()
        for method in methods:
            for sub in ast.walk(method):
                if (
                    isinstance(sub, ast.Attribute)
                    and isinstance(sub.ctx, ast.Store)
                    and isinstance(sub.value, ast.Name)
                    and sub.value.id == "self"
                ):
                    attrs.add(sub.attr)
        if len(methods) > MAX_CLASS_METHODS or len(attrs) > MAX_CLASS_ATTRS:
            severity = (
                "error"
                if len(methods) > MAX_CLASS_METHODS * 1.5 or len(attrs) > MAX_CLASS_ATTRS * 1.5
                else "warning"
            )
            issues.append(
                Issue(
                    file=filepath,
                    line=node.lineno,
                    severity=severity,
                    rule="god-class",
                    package=pkg,
                    message=(
                        f"class {node.name} has {len(methods)} methods and {len(attrs)} attributes "
                        f"(max {MAX_CLASS_METHODS}/{MAX_CLASS_ATTRS}) — consider splitting responsibilities"
                    ),
                )
            )
    return issues


# ── Rule: Layer Violations ────────────────────────────────────────────────────


def check_layer_violations(tree: ast.Module, filepath: Path, pkg: str) -> list[Issue]:
    """Enforce architectural layer boundaries."""
    # Late import so tests can monkeypatch detector.PACKAGES
    import spaghetti.detector as _det

    rules = LAYER_RULES.get(pkg, {})
    if not rules:
        return []
    pkg_root = _det.PACKAGES.get(pkg)
    if pkg_root is None:
        return []

    issues: list[Issue] = []
    rel_path = str(filepath.relative_to(pkg_root))

    for pattern, forbidden_prefixes in rules.items():
        if not rel_path.startswith(pattern):
            continue
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                imported = ""
                if isinstance(node, ast.ImportFrom) and node.module:
                    imported = node.module
                elif isinstance(node, ast.Import):
                    for alias in node.names:
                        imported = alias.name
                        break

                for prefix in forbidden_prefixes:
                    if imported.startswith(prefix):
                        issues.append(
                            Issue(
                                file=filepath,
                                line=node.lineno,
                                severity="error",
                                rule="layer-violation",
                                package=pkg,
                                message=f"Module '{rel_path}' imports '{imported}' — forbidden by layer rules",
                            )
                        )
    return issues


# ── Rule: Transport in Library ────────────────────────────────────────────────


def check_transport_in_library(tree: ast.Module, filepath: Path, pkg: str) -> list[Issue]:
    """Detect FastAPI/HTTP imports in library packages."""
    if pkg not in ("etl-core", "boti", "boti-data", "boti-dask"):
        return []

    issues: list[Issue] = []
    transport_modules = {"fastapi", "starlette", "httpx", "flask", "django"}

    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            top = node.module.split(".")[0]
            if top in transport_modules:
                issues.append(
                    Issue(
                        file=filepath,
                        line=node.lineno,
                        severity="error",
                        rule="transport-in-library",
                        package=pkg,
                        message=f"Library imports transport module '{top}' — violates G9",
                    )
                )
    return issues


# ── Rule: Circular Imports (per-file heuristic) ──────────────────────────────


def check_circular_imports(tree: ast.Module, filepath: Path, pkg: str) -> list[Issue]:
    """Detect within-package circular imports (child importing parent)."""
    # Late import so tests can monkeypatch detector.PACKAGES
    import spaghetti.detector as _det

    pkg_root = _det.PACKAGES.get(pkg)
    if pkg_root is None:
        return []

    issues: list[Issue] = []
    module_name = filepath.relative_to(pkg_root.parent).with_suffix("")
    parts = list(module_name.parts)

    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            imp_parts = node.module.split(".")
            if len(imp_parts) < len(parts) and parts[: len(imp_parts)] == imp_parts:
                issues.append(
                    Issue(
                        file=filepath,
                        line=node.lineno,
                        severity="warning",
                        rule="potential-circular-import",
                        package=pkg,
                        message=f"Child module imports parent '{node.module}' — potential circular dependency",
                    )
                )
    return issues


# ── Rule: Mutable Default Arguments ───────────────────────────────────────────


def check_mutable_defaults(tree: ast.Module, filepath: Path, pkg: str) -> list[Issue]:
    """Detect mutable default arguments (list, dict, set)."""
    issues: list[Issue] = []
    mutable_types = (ast.List, ast.Dict, ast.Set)

    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for default in node.args.defaults + node.args.kw_defaults:
                if default is not None and isinstance(default, mutable_types):
                    issues.append(
                        Issue(
                            file=filepath,
                            line=node.lineno,
                            severity="warning",
                            rule="mutable-default",
                            package=pkg,
                            message=f"{node.name}() has mutable default argument — use None instead",
                        )
                    )
    return issues


# ── Rule: Bare Except ─────────────────────────────────────────────────────────


def check_bare_except(tree: ast.Module, filepath: Path, pkg: str) -> list[Issue]:
    """Detect bare except clauses."""
    issues: list[Issue] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ExceptHandler) and node.type is None:
            issues.append(
                Issue(
                    file=filepath,
                    line=node.lineno,
                    severity="warning",
                    rule="bare-except",
                    package=pkg,
                    message="Bare except clause — catch specific exceptions instead",
                )
            )
    return issues


# ── Rule: Star Imports ────────────────────────────────────────────────────────


def check_star_imports(tree: ast.Module, filepath: Path, pkg: str) -> list[Issue]:
    """Detect ``from x import *``."""
    issues: list[Issue] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            for alias in node.names:
                if alias.name == "*":
                    issues.append(
                        Issue(
                            file=filepath,
                            line=node.lineno,
                            severity="warning",
                            rule="star-import",
                            package=pkg,
                            message=f"Star import from '{node.module}' — import specific names instead",
                        )
                    )
    return issues


# ── Rule: Global State Mutation ───────────────────────────────────────────────


def check_global_mutations(tree: ast.Module, filepath: Path, pkg: str) -> list[Issue]:
    """Detect module-level mutable state (global list/dict)."""
    issues: list[Issue] = []
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and isinstance(
                    node.value, (ast.List, ast.Dict, ast.Set)
                ):
                    if not target.id.startswith("_"):
                        issues.append(
                            Issue(
                                file=filepath,
                                line=node.lineno,
                                severity="info",
                                rule="global-mutable",
                                package=pkg,
                                message=f"Module-level mutable '{target.id}' — consider encapsulating",
                            )
                        )
    return issues


# ── Rule: Scope Mutation ──────────────────────────────────────────────────────


def check_scope_mutations(tree: ast.Module, filepath: Path, pkg: str) -> list[Issue]:
    """Detect functions that explicitly mutate outer-scope variables via
    ``global`` or ``nonlocal`` declarations followed by assignments."""
    issues: list[Issue] = []

    def _walk_own_scope(node: ast.AST):
        """Yield nodes in this scope only — stop at nested function/class defs."""
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                continue
            yield child
            yield from _walk_own_scope(child)

    for func_node in ast.walk(tree):
        if not isinstance(func_node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue

        outer_names: set[str] = set()
        for child in _walk_own_scope(func_node):
            if isinstance(child, ast.Global):
                outer_names.update(child.names)
            elif isinstance(child, ast.Nonlocal):
                outer_names.update(child.names)

        if not outer_names:
            continue

        for child in _walk_own_scope(func_node):
            target_name: str | None = None
            if isinstance(child, ast.Assign):
                for t in child.targets:
                    if isinstance(t, ast.Name) and t.id in outer_names:
                        target_name = t.id
                        break
            elif isinstance(child, ast.AugAssign) and isinstance(child.target, ast.Name):
                if child.target.id in outer_names:
                    target_name = child.target.id

            if target_name is None:
                continue

            declared_by: set[str] = set()
            for sub in _walk_own_scope(func_node):
                if isinstance(sub, ast.Global) and target_name in sub.names:
                    declared_by.add("global")
                elif isinstance(sub, ast.Nonlocal) and target_name in sub.names:
                    declared_by.add("nonlocal")
            keyword_str = "/".join(sorted(declared_by))

            issues.append(
                Issue(
                    file=filepath,
                    line=child.lineno,
                    severity="info",
                    rule="scope-mutation",
                    package=pkg,
                    message=(
                        f"{func_node.name}() mutates outer-scope variable "
                        f"'{target_name}' via {keyword_str} — shared mutable "
                        f"state makes control flow hard to trace"
                    ),
                )
            )
            break  # one finding per function is enough

    return issues


# ── Gap-Analysis Rules ────────────────────────────────────────────────────────


# Statement types that unconditionally terminate the control flow of the
# block they're in — used by both check_dead_code (what follows one of these
# is unreachable) and check_missing_else (an `if` body ending in one of these
# needs no explicit negative path: it's either "the rest of the function" or
# "the next loop iteration").
_CONTROL_FLOW_TERMINAL_STMT_TYPES = (ast.Return, ast.Raise, ast.Break, ast.Continue)


def check_dead_code(tree: ast.Module, filepath: Path, pkg: str) -> list[Issue]:
    """Flag statements that are guaranteed unreachable because they follow
    ``return``, ``raise``, ``break``, or ``continue``."""
    issues: list[Issue] = []

    def _unreachable_after(stmt: ast.stmt) -> bool:
        return isinstance(stmt, _CONTROL_FLOW_TERMINAL_STMT_TYPES)

    def _scan_body(body: list[ast.stmt]) -> None:
        for i, stmt in enumerate(body):
            if _unreachable_after(stmt):
                for j in range(i + 1, len(body)):
                    issues.append(
                        Issue(
                            file=filepath,
                            line=getattr(body[j], "lineno", stmt.lineno),
                            severity="warning",
                            rule="dead-code",
                            package=pkg,
                            message="statement is unreachable — previous line always terminates",
                        )
                    )
                break

    def _scan_stmt(stmt: ast.stmt) -> None:
        for attr in ("body", "orelse"):
            block = getattr(stmt, attr, None)
            if block:
                _scan_body(block)
        if isinstance(stmt, ast.Try):
            for handler in stmt.handlers:
                _scan_body(handler.body)

    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            _scan_body(node.body)
            for stmt in node.body:
                _scan_stmt(stmt)

    return issues


def check_message_chains(tree: ast.Module, filepath: Path, pkg: str) -> list[Issue]:
    """Flag method / attribute chains deeper than MAX_MESSAGE_CHAIN_DEPTH."""
    issues: list[Issue] = []

    def _chain_depth(node: ast.expr) -> int:
        if isinstance(node, ast.Call):
            return _chain_depth(node.func)
        if isinstance(node, ast.Attribute):
            return 1 + _chain_depth(node.value)
        return 0

    def _scan(node: ast.AST) -> None:
        for child in ast.walk(node):
            if isinstance(child, ast.Attribute) or isinstance(child, ast.Call):
                depth = _chain_depth(child)
                if depth > MAX_MESSAGE_CHAIN_DEPTH:
                    issues.append(
                        Issue(
                            file=filepath,
                            line=child.lineno,
                            severity="info",
                            rule="message-chain",
                            package=pkg,
                            message=f"method/attribute chain depth {depth} exceeds {MAX_MESSAGE_CHAIN_DEPTH} — split into intermediate variables",
                        )
                    )
                    break

    for node in ast.iter_child_nodes(tree):
        _scan(node)

    return issues


def check_excessive_decorators(tree: ast.Module, filepath: Path, pkg: str) -> list[Issue]:
    """Flag functions or classes with more than MAX_DECORATORS decorators."""
    issues: list[Issue] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            n_decorators = len(node.decorator_list)
            if n_decorators > MAX_DECORATORS:
                kind = "class" if isinstance(node, ast.ClassDef) else "function"
                issues.append(
                    Issue(
                        file=filepath,
                        line=node.lineno,
                        severity="info",
                        rule="excessive-decorators",
                        package=pkg,
                        message=f"{kind} '{node.name}' has {n_decorators} decorators (max {MAX_DECORATORS}) — consider a wrapper or composition",
                    )
                )
    return issues


def check_magic_numbers(tree: ast.Module, filepath: Path, pkg: str) -> list[Issue]:
    """Flag numeric literals other than 0, 1, or -1. ``__init__`` methods are skipped."""
    _ALLOWED = {-1, 0, 1}
    issues: list[Issue] = []

    def _scan_func(func: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        if func.name == "__init__":
            return
        for child in ast.walk(func):
            if isinstance(child, ast.Constant) and isinstance(child.value, (int, float)):
                if child.value not in _ALLOWED:
                    issues.append(
                        Issue(
                            file=filepath,
                            line=child.lineno,
                            severity="info",
                            rule="magic-number",
                            package=pkg,
                            message=f"magic number {child.value!r} — extract to a named constant",
                        )
                    )

    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            _scan_func(node)

    return issues


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
        if isinstance(node.body[-1], _CONTROL_FLOW_TERMINAL_STMT_TYPES):
            continue
        issues.append(
            Issue(
                file=filepath,
                line=node.lineno,
                severity="info",
                rule="missing-else",
                package=pkg,
                message=f"'if' block has {len(node.body)} statements but no else/elif — missing the negative path",
            )
        )

    return issues


def check_lazy_class(tree: ast.Module, filepath: Path, pkg: str) -> list[Issue]:
    """Flag classes with fewer than 2 methods."""
    issues: list[Issue] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue
        methods = [n for n in node.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
        if len(methods) < 2:
            issues.append(
                Issue(
                    file=filepath,
                    line=node.lineno,
                    severity="info",
                    rule="lazy-class",
                    package=pkg,
                    message=f"class '{node.name}' has {len(methods)} method(s) — consider a plain function or @dataclass",
                )
            )
    return issues


def check_deep_inheritance(tree: ast.Module, filepath: Path, pkg: str) -> list[Issue]:
    """Flag classes whose effective inheritance depth exceeds MAX_INHERITANCE_DEPTH."""
    issues: list[Issue] = []

    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue
        if not node.bases:
            continue
        seen: set[str] = set()
        queue: list[str] = []
        for base in node.bases:
            if isinstance(base, ast.Name):
                queue.append(base.id)
            elif isinstance(base, ast.Attribute):
                queue.append(base.attr)
        while queue:
            name = queue.pop(0)
            if name in seen or name == node.name:
                continue
            seen.add(name)
            for other in ast.walk(tree):
                if isinstance(other, ast.ClassDef) and other.name == name:
                    for base in other.bases:
                        if isinstance(base, ast.Name):
                            queue.append(base.id)
                        elif isinstance(base, ast.Attribute):
                            queue.append(base.attr)
        total_depth = len(seen)
        if total_depth >= MAX_INHERITANCE_DEPTH:
            issues.append(
                Issue(
                    file=filepath,
                    line=node.lineno,
                    severity="warning",
                    rule="deep-inheritance",
                    package=pkg,
                    message=(
                        f"class '{node.name}' has effective inheritance depth "
                        f"{total_depth} (max {MAX_INHERITANCE_DEPTH}) — use composition"
                    ),
                )
            )
    return issues


# ── Rule: God Module ──────────────────────────────────────────────────────────


def check_god_module(tree: ast.Module, filepath: Path, pkg: str) -> list[Issue]:
    """Detect modules with too many public classes/functions."""
    issues: list[Issue] = []
    public_classes = 0
    public_funcs = 0

    for node in ast.iter_child_nodes(tree):
        if isinstance(node, ast.ClassDef) and not node.name.startswith("_"):
            public_classes += 1
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and not node.name.startswith(
            "_"
        ):
            public_funcs += 1

    total = public_classes + public_funcs
    if total > 15:
        issues.append(
            Issue(
                file=filepath,
                line=1,
                severity="warning",
                rule="god-module",
                package=pkg,
                message=f"Module exposes {total} public symbols ({public_classes} classes, {public_funcs} functions) — consider splitting",
            )
        )
    return issues


# ── Rule: Pass-Through Methods ────────────────────────────────────────────────


def check_pass_through_methods(tree: ast.Module, filepath: Path, pkg: str) -> list[Issue]:
    """Detect methods that do nothing but delegate to another function."""
    issues: list[Issue] = []

    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue

        if node.name.startswith("__") and node.name.endswith("__"):
            continue

        body = [
            stmt
            for stmt in node.body
            if not (
                isinstance(stmt, ast.Expr)
                and isinstance(stmt.value, ast.Constant)
                and isinstance(stmt.value.value, str)
            )
        ]

        if len(body) != 1:
            continue

        stmt = body[0]

        call_node = None
        if isinstance(stmt, ast.Return):
            if isinstance(stmt.value, ast.Call):
                call_node = stmt.value
            elif isinstance(stmt.value, ast.Await) and isinstance(stmt.value.value, ast.Call):
                call_node = stmt.value.value
        elif isinstance(stmt, ast.Expr):
            if isinstance(stmt.value, ast.Call):
                call_node = stmt.value
            elif isinstance(stmt.value, ast.Await) and isinstance(stmt.value.value, ast.Call):
                call_node = stmt.value.value

        if not call_node:
            continue

        if (
            isinstance(call_node.func, ast.Attribute)
            and isinstance(call_node.func.value, ast.Call)
            and getattr(call_node.func.value.func, "id", "") == "super"
        ):
            continue

        is_pure_delegation = all(isinstance(a, (ast.Name, ast.Starred)) for a in call_node.args)

        if is_pure_delegation:
            is_pure_delegation = all(isinstance(k.value, ast.Name) for k in call_node.keywords)

        if is_pure_delegation:
            target = ast.unparse(call_node.func)
            issues.append(
                Issue(
                    file=filepath,
                    line=node.lineno,
                    severity="info",
                    rule="pass-through-method",
                    package=pkg,
                    message=(
                        f"{node.name}() is a pure pass-through to '{target}()'. "
                        "Consider exposing the underlying object."
                    ),
                )
            )

    return issues


# ── Check registry ────────────────────────────────────────────────────────────

ALL_CHECKS: list[FileCheck] = [
    check_long_functions,
    check_complexity,
    check_missing_types,
    check_excessive_params,
    check_excessive_returns,
    check_boolean_flag_params,
    check_deep_nesting,
    check_untyped_dicts,
    check_unused_imports,
    check_swallowed_exceptions,
    check_duplicate_branches,
    check_encapsulation_violations,
    check_god_class,
    check_layer_violations,
    check_transport_in_library,
    check_circular_imports,
    check_god_module,
    check_mutable_defaults,
    check_bare_except,
    check_star_imports,
    check_global_mutations,
    check_scope_mutations,
    check_dead_code,
    check_message_chains,
    check_excessive_decorators,
    check_magic_numbers,
    check_missing_else,
    check_lazy_class,
    check_deep_inheritance,
    check_pass_through_methods,
]
