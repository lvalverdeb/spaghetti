"""Per-file AST-based checks — the core 27 rules."""

from __future__ import annotations

import ast
from collections import defaultdict
from collections.abc import Callable, Iterator
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
    ERROR_ESCALATION_MULTIPLIER,
    LAYER_RULES,
    MAX_CLASS_ATTRS,
    MAX_CLASS_METHODS,
    MAX_DECORATORS,
    MAX_FUNC_PARAMS,
    MAX_FUNCTION_LINES,
    MAX_INHERITANCE_DEPTH,
    MAX_MESSAGE_CHAIN_DEPTH,
    MAX_NESTING_DEPTH,
    MAX_PUBLIC_SYMBOLS,
    MAX_RETURNS,
    MIN_BOOLEAN_FLAGS,
    MIN_CLASS_METHODS,
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
                        message=(
                            f"{node.name}() is {lines} lines (max {MAX_FUNCTION_LINES}) — "
                            "extract logical chunks into named helper functions (Extract "
                            "Method)"
                        ),
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
                        severity=(
                            "error"
                            if cc > COMPLEXITY_THRESHOLD * ERROR_ESCALATION_MULTIPLIER
                            else "warning"
                        ),
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
                        message=(
                            f"{node.name}() has {total} params (max {MAX_FUNC_PARAMS}) — "
                            "consider a Parameter Object (a dataclass or Pydantic model "
                            "bundling the related fields) instead"
                        ),
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
                        message=(
                            f"{node.name}() has {n_returns} return statements "
                            f"(max {MAX_RETURNS}) — consider a Return Object bundling the "
                            "result and building it up to a single return"
                        ),
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
                            f"({', '.join(flags)}) — combinations multiply branching; "
                            "consider the Strategy pattern (inject the varying behavior "
                            "as an object) instead of flag-branching for it"
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
                        message=(
                            f"{node.name}() has nesting depth {depth} (max {MAX_NESTING_DEPTH}) "
                            "— use guard clauses (invert the condition, exit early) to keep "
                            "the happy path unindented"
                        ),
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
                        message=(
                            "Bare 'dict' used in type hint — use dict[str, Any] or similar, "
                            "a dataclass/Pydantic model (a DTO) if the shape is fixed, or "
                            "typing.TypedDict if it must stay a plain dict at runtime"
                        ),
                    )
                )

    unique_issues = {(i.line, i.rule): i for i in issues}
    return list(unique_issues.values())


# ── Rule: Unused Imports ──────────────────────────────────────────────────────


def _collect_imported_names(tree: ast.Module) -> dict[str, int]:
    """Map each name an ``import``/``from ... import`` statement binds to the
    line it was imported on. ``*`` and ``__future__`` imports don't bind a
    real name; ``_`` is the conventional "I don't care about this" sink."""
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
    return imported


def _collect_used_names(tree: ast.Module) -> set[str]:
    """Every name referenced by the module — either directly, or listed in
    an ``__all__ = [...]`` re-export declaration."""
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
    return used


def check_unused_imports(tree: ast.Module, filepath: Path, pkg: str) -> list[Issue]:
    """Flags imported names never referenced in the file.
    ``__init__.py`` is skipped (re-exports)."""
    if filepath.name == "__init__.py":
        return []

    imported = _collect_imported_names(tree)
    if not imported:
        return []

    used = _collect_used_names(tree)

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

# getattr(obj, name)/setattr(obj, name, value)/hasattr(obj, name) all take the
# attribute-name argument in position 1 — reflective access can't be checked
# without at least that many positional args.
_MIN_REFLECTIVE_ACCESS_ARGS = 2


def _is_allowed_private_access_base(base: ast.AST, class_name: str | None) -> bool:
    """True if *base* is ``self``/``cls``/the enclosing class/``super()`` —
    i.e. a private member reached through it is *not* an encapsulation
    violation."""
    if isinstance(base, ast.Name) and base.id in ("self", "cls", class_name):
        return True
    return (
        isinstance(base, ast.Call) and isinstance(base.func, ast.Name) and base.func.id == "super"
    )


def _is_private_name(name: str) -> bool:
    return name.startswith("_") and not _DUNDER_RE.match(name)


def _direct_private_access(node: ast.AST, class_name: str | None) -> str | None:
    """The private attribute name reached via ``obj._attr``, or None."""
    if not (isinstance(node, ast.Attribute) and isinstance(node.ctx, ast.Load)):
        return None
    if not _is_private_name(node.attr) or _is_allowed_private_access_base(node.value, class_name):
        return None
    return node.attr


def _reflective_private_access(node: ast.AST, class_name: str | None) -> tuple[str, str] | None:
    """The ``(func_name, attr_name)`` reached via ``getattr(obj, "_attr")``
    (or ``setattr``/``hasattr``), or None."""
    if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)):
        return None
    if (
        node.func.id not in ("getattr", "setattr", "hasattr")
        or len(node.args) < _MIN_REFLECTIVE_ACCESS_ARGS
    ):
        return None
    attr_arg = node.args[1]
    if not (isinstance(attr_arg, ast.Constant) and isinstance(attr_arg.value, str)):
        return None
    if not _is_private_name(attr_arg.value) or _is_allowed_private_access_base(
        node.args[0], class_name
    ):
        return None
    return node.func.id, attr_arg.value


def check_encapsulation_violations(tree: ast.Module, filepath: Path, pkg: str) -> list[Issue]:
    """Reaching into another object's private (``_name``) attribute from outside
    self/cls/its own class."""
    issues: list[Issue] = []

    for node, class_name in _walk_with_class_context(tree):
        direct_attr = _direct_private_access(node, class_name)
        if direct_attr is not None:
            issues.append(
                Issue(
                    file=filepath,
                    line=node.lineno,
                    severity="info",
                    rule="encapsulation-violation",
                    package=pkg,
                    message=f"Accesses private member '.{direct_attr}' through something other than self/cls",
                )
            )
            continue

        reflective = _reflective_private_access(node, class_name)
        if reflective is not None:
            func_name, reflective_attr = reflective
            issues.append(
                Issue(
                    file=filepath,
                    line=node.lineno,
                    severity="info",
                    rule="encapsulation-violation",
                    package=pkg,
                    message=f"{func_name}(..., '{reflective_attr}', ...) reaches into a private attribute",
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
                if len(methods) > MAX_CLASS_METHODS * ERROR_ESCALATION_MULTIPLIER
                or len(attrs) > MAX_CLASS_ATTRS * ERROR_ESCALATION_MULTIPLIER
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
                                message=(
                                    f"Module '{rel_path}' imports '{imported}' — forbidden by "
                                    "layer rules; depend on an abstraction (e.g. a "
                                    "typing.Protocol) the lower layer implements, injected as "
                                    "a constructor/function parameter instead of imported "
                                    "directly (Dependency Inversion Principle / Dependency "
                                    "Injection)"
                                ),
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
                        message=(
                            f"Library imports transport module '{top}' — violates G9; "
                            "depend on an abstraction (e.g. a typing.Protocol) instead of "
                            "the concrete transport, injected as a parameter rather than "
                            "imported directly (Dependency Inversion Principle / Dependency "
                            "Injection)"
                        ),
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
                        message=(
                            f"Child module imports parent '{node.module}' — potential "
                            "circular dependency; extract a shared abstraction (e.g. a "
                            "typing.Protocol) both sides can depend on, and inject it "
                            "instead of importing directly to break the cycle "
                            "(Dependency Inversion Principle / Dependency Injection)"
                        ),
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
                                message=(
                                    f"Module-level mutable '{target.id}' — consider "
                                    "encapsulating it in a class and injecting it where "
                                    "needed instead of reaching for module-level global "
                                    "state (Dependency Injection)"
                                ),
                            )
                        )
    return issues


# ── Rule: Scope Mutation ──────────────────────────────────────────────────────


def _walk_own_scope(node: ast.AST) -> Iterator[ast.AST]:
    """Yield nodes in this scope only — stop at nested function/class defs."""
    for child in ast.iter_child_nodes(node):
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        yield child
        yield from _walk_own_scope(child)


def check_scope_mutations(tree: ast.Module, filepath: Path, pkg: str) -> list[Issue]:
    """Detect functions that explicitly mutate outer-scope variables via
    ``global`` or ``nonlocal`` declarations followed by assignments."""
    issues: list[Issue] = []

    for func_node in ast.walk(tree):
        if not isinstance(func_node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue

        # Every outer-scope name this function declares, mapped to which
        # keyword(s) declared it — computed once so the mutation scan below
        # doesn't need to re-walk the scope per candidate to answer
        # "global, nonlocal, or both?".
        declared_by: dict[str, set[str]] = defaultdict(set)
        for child in _walk_own_scope(func_node):
            if isinstance(child, ast.Global):
                for name in child.names:
                    declared_by[name].add("global")
            elif isinstance(child, ast.Nonlocal):
                for name in child.names:
                    declared_by[name].add("nonlocal")

        if not declared_by:
            continue

        for child in _walk_own_scope(func_node):
            target_name: str | None = None
            if isinstance(child, ast.Assign):
                for t in child.targets:
                    if isinstance(t, ast.Name) and t.id in declared_by:
                        target_name = t.id
                        break
            elif isinstance(child, ast.AugAssign) and isinstance(child.target, ast.Name):
                if child.target.id in declared_by:
                    target_name = child.target.id

            if target_name is None:
                continue

            keyword_str = "/".join(sorted(declared_by[target_name]))

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
    """Flag numeric literals other than 0, 1, or -1 in a function's body.

    Skips the function's own signature — a default parameter value (e.g.
    ``base_delay: float = 0.5``) is already named by the parameter itself —
    and skips a literal passed as a call's keyword argument (e.g.
    ``stacklevel=2``), since the keyword name documents it the same way a
    named constant would. ``__init__`` methods are skipped entirely.
    """
    _ALLOWED = {-1, 0, 1}
    issues: list[Issue] = []

    def _scan_func(func: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        if func.name == "__init__":
            return
        keyword_value_ids = {
            id(kw.value)
            for stmt in func.body
            for kw in ast.walk(stmt)
            if isinstance(kw, ast.keyword)
        }
        for stmt in func.body:
            for child in ast.walk(stmt):
                if not (isinstance(child, ast.Constant) and isinstance(child.value, (int, float))):
                    continue
                if id(child) in keyword_value_ids or child.value in _ALLOWED:
                    continue
                issues.append(
                    Issue(
                        file=filepath,
                        line=child.lineno,
                        severity="info",
                        rule="magic-number",
                        package=pkg,
                        message=(
                            f"magic number {child.value!r} — extract to a named constant, "
                            "or an enum.IntEnum if it's one of a fixed set of status/"
                            "category codes"
                        ),
                    )
                )

    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            _scan_func(node)

    return issues


# ── Rule: Magic Strings ────────────────────────────────────────────────────────

# A string compared exactly once is an ordinary literal; it only looks like
# an ad-hoc category/status code once the *same* value is compared in
# multiple places, which is the actual "scattered, fragile equality check"
# signal this rule is after.
_MIN_MAGIC_STRING_OCCURRENCES = 2

# Single characters (``"_"``, ``"*"``, ``"."``) are almost always punctuation/
# wildcard tokens, never a category/status code — exclude them rather than
# flag every AST-walking tool's inevitable comparisons against them.
_MIN_MAGIC_STRING_LENGTH = 2


def _string_operand(node: ast.expr) -> str | None:
    """The string value of *node* if it's a "long enough" string constant,
    else None."""
    if (
        isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and len(node.value) >= _MIN_MAGIC_STRING_LENGTH
    ):
        return node.value
    return None


# Fields Python's own `ast` module uses to hold identifier strings: keyword
# argument names (`keyword.arg`), variable names (`Name.id`), attribute
# names (`Attribute.attr`). Equality checks against these are AST-shape
# matching (e.g. `kw.arg == "allow_pickle"` to find a specific call
# signature), not the stringly-typed business logic this rule targets —
# excluding them avoids false positives in any AST-walking tool comparing
# against known field/argument names.
_AST_IDENTIFIER_FIELDS = frozenset({"arg", "id", "attr"})


def _is_ast_identifier_field_access(node: ast.expr) -> bool:
    return isinstance(node, ast.Attribute) and node.attr in _AST_IDENTIFIER_FIELDS


# A dunder name needs at least one character between the double
# underscores (e.g. `__init__`) — bare underscore runs like `____` are just
# punctuation, not Python's magic-method vocabulary.
_MIN_DUNDER_NAME_LENGTH = 4


def _is_dunder_name(value: str) -> bool:
    """True for Python's own magic-method/attribute vocabulary (`__init__`,
    `__new__`, `__call__`, ...). These are reflection/introspection
    artifacts, never a business category code, regardless of what
    attribute holds them — so a comparison like `name == "__init__"` isn't
    the stringly-typed smell this rule targets, unlike the general `.name`
    field (too common in ordinary business code to exclude wholesale)."""
    return len(value) > _MIN_DUNDER_NAME_LENGTH and value.startswith("__") and value.endswith("__")


def _is_excluded_magic_string_value(value: str, other_operand: ast.expr) -> bool:
    """True when this string/other-operand pairing is a known non-business
    comparison (AST-shape matching or Python's own dunder vocabulary) that
    check_magic_strings should ignore."""
    return _is_dunder_name(value) or _is_ast_identifier_field_access(other_operand)


def _magic_string_comparison(node: ast.Compare) -> tuple[str, int] | None:
    """The (value, lineno) pair for *node* if it's a non-excluded
    `str == <expr>` / `<expr> == str` equality comparison, else None."""
    if len(node.ops) != 1 or not isinstance(node.ops[0], (ast.Eq, ast.NotEq)):
        return None
    left, right = _string_operand(node.left), _string_operand(node.comparators[0])
    if left is not None and right is None:
        if _is_excluded_magic_string_value(left, node.comparators[0]):
            return None
        return left, node.lineno
    if right is not None and left is None:
        if _is_excluded_magic_string_value(right, node.left):
            return None
        return right, node.lineno
    return None


def check_magic_strings(tree: ast.Module, filepath: Path, pkg: str) -> list[Issue]:
    """Flag string literals repeatedly compared for equality against a
    variable/expression — a sign the value is being used as an ad-hoc
    category or status code (with fragile, scattered `==`/`.upper()`-style
    handling) rather than a proper Value Object that canonicalizes it once.
    """
    comparisons: list[tuple[str, int]] = []

    for node in ast.walk(tree):
        if not isinstance(node, ast.Compare):
            continue
        result = _magic_string_comparison(node)
        if result is not None:
            comparisons.append(result)

    counts: dict[str, int] = {}
    for value, _ in comparisons:
        counts[value] = counts.get(value, 0) + 1

    issues: list[Issue] = []
    for value, lineno in comparisons:
        if counts[value] >= _MIN_MAGIC_STRING_OCCURRENCES:
            issues.append(
                Issue(
                    file=filepath,
                    line=lineno,
                    severity="info",
                    rule="magic-string",
                    package=pkg,
                    message=(
                        f"magic string {value!r} compared {counts[value]} times — consider "
                        "a Value Object that canonicalizes it once (e.g. a Pydantic model "
                        "with a @field_validator) instead of repeated string comparisons"
                    ),
                )
            )
    return issues


# A single-statement `if` body has no meaningful "negative path" to miss —
# only bodies at or above this length are flagged by check_missing_else.
_NON_TRIVIAL_BODY_THRESHOLD = 2


def check_missing_else(tree: ast.Module, filepath: Path, pkg: str) -> list[Issue]:
    """Flag ``if`` blocks with 2+ statements but no ``else``/``elif``.

    Skipped when the ``if`` body's last statement already terminates control
    flow (``return``/``raise``/``continue``/``break``): the negative path is
    either "the rest of the function" or "the next loop iteration", and is
    not missing.

    Also skipped when the body's last statement is itself a bare ``if``
    (no ``elif``/``else`` of its own), a discarded-return call expression
    (e.g. ``issues.append(...)``), or a ``for``/``while`` loop. All three
    shapes — some setup then a single trailing conditional, side-effect
    call, or iteration — mean the ``if`` exists only to *guard entry* into
    that final step (e.g. "if this is the right node type: compute X,
    then record it" / "...then check each of its sub-elements"), not to
    encode two real branches of logic. The "negative path" is just "skip
    this node", which is already what happens without an else.
    """
    issues: list[Issue] = []

    for node in ast.walk(tree):
        if not isinstance(node, ast.If):
            continue
        if node.orelse:
            continue
        if len(node.body) < _NON_TRIVIAL_BODY_THRESHOLD:
            continue
        if isinstance(node.body[-1], _CONTROL_FLOW_TERMINAL_STMT_TYPES):
            continue
        if isinstance(node.body[-1], ast.If) and not node.body[-1].orelse:
            continue
        if isinstance(node.body[-1], (ast.For, ast.AsyncFor, ast.While)):
            continue
        if isinstance(node.body[-1], ast.Expr) and isinstance(node.body[-1].value, ast.Call):
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


# Base class names (matched by their final ast.Name/ast.Attribute component,
# not a resolved import) that already make a class a declarative data
# container — flagging them as "lazy" and suggesting "@dataclass" is
# nonsensical since they already fulfill that exact role.
_LAZY_CLASS_EXEMPT_BASE_NAMES = frozenset({"BaseModel", "BaseSettings", "NamedTuple"})


def _lazy_class_decorator_target_name(dec: ast.expr) -> str | None:
    """The name a decorator resolves to, e.g. 'dataclass' for both
    ``@dataclass`` and ``@dataclass(frozen=True)``."""
    target = dec.func if isinstance(dec, ast.Call) else dec
    if isinstance(target, ast.Name):
        return target.id
    if isinstance(target, ast.Attribute):
        return target.attr
    return None


def _lazy_class_is_exempt(node: ast.ClassDef) -> bool:
    """True if *node* already is a declarative data container.

    Pydantic ``BaseModel``/``BaseSettings`` subclasses and
    ``@dataclass``-decorated classes already satisfy check_lazy_class's own
    suggested remedy, so they should never be flagged regardless of method
    count.
    """
    for base in node.bases:
        name = base.id if isinstance(base, ast.Name) else getattr(base, "attr", None)
        if name in _LAZY_CLASS_EXEMPT_BASE_NAMES:
            return True
    return any(_lazy_class_decorator_target_name(dec) == "dataclass" for dec in node.decorator_list)


def check_lazy_class(tree: ast.Module, filepath: Path, pkg: str) -> list[Issue]:
    """Flag classes with fewer than 2 methods.

    Skipped for classes that already are declarative data containers — see
    ``_lazy_class_is_exempt``.
    """
    issues: list[Issue] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue
        if _lazy_class_is_exempt(node):
            continue
        methods = [n for n in node.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
        if len(methods) < MIN_CLASS_METHODS:
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


def _base_names(bases: list[ast.expr]) -> list[str]:
    """Base-class names from a ``ClassDef``'s ``bases`` — dotted names
    collapsed to their final attribute (e.g. ``pkg.Base`` -> ``Base``)."""
    names: list[str] = []
    for base in bases:
        if isinstance(base, ast.Name):
            names.append(base.id)
        elif isinstance(base, ast.Attribute):
            names.append(base.attr)
    return names


def check_deep_inheritance(tree: ast.Module, filepath: Path, pkg: str) -> list[Issue]:
    """Flag classes whose effective inheritance depth exceeds MAX_INHERITANCE_DEPTH."""
    issues: list[Issue] = []

    # Indexed once so the BFS below does O(1) name lookups instead of
    # re-walking the whole module tree for every ancestor it discovers.
    classes_by_name: dict[str, list[ast.ClassDef]] = defaultdict(list)
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            classes_by_name[node.name].append(node)

    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue
        if not node.bases:
            continue
        seen: set[str] = set()
        queue: list[str] = _base_names(node.bases)
        while queue:
            name = queue.pop(0)
            if name in seen or name == node.name:
                continue
            seen.add(name)
            for other in classes_by_name.get(name, ()):
                queue.extend(_base_names(other.bases))
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
                        f"{total_depth} (max {MAX_INHERITANCE_DEPTH}) — use composition, "
                        "e.g. the Strategy pattern, instead of another inheritance level"
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
    if total > MAX_PUBLIC_SYMBOLS:
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


def _non_docstring_body(node: ast.FunctionDef | ast.AsyncFunctionDef) -> list[ast.stmt]:
    return [
        stmt
        for stmt in node.body
        if not (
            isinstance(stmt, ast.Expr)
            and isinstance(stmt.value, ast.Constant)
            and isinstance(stmt.value.value, str)
        )
    ]


def _call_from_stmt(stmt: ast.stmt) -> ast.Call | None:
    """The `ast.Call` a single `return`/expression statement evaluates,
    unwrapping a single `await` if present, else None."""
    value: ast.expr | None = None
    if isinstance(stmt, ast.Return):
        value = stmt.value
    elif isinstance(stmt, ast.Expr):
        value = stmt.value
    if isinstance(value, ast.Await):
        value = value.value
    return value if isinstance(value, ast.Call) else None


def _is_super_call(call_node: ast.Call) -> bool:
    return (
        isinstance(call_node.func, ast.Attribute)
        and isinstance(call_node.func.value, ast.Call)
        and getattr(call_node.func.value.func, "id", "") == "super"
    )


def _is_pure_delegation_call(call_node: ast.Call) -> bool:
    """True if every argument is forwarded unchanged (a bare name or
    `*args`/`**kwargs`), not transformed or computed."""
    if not all(isinstance(a, (ast.Name, ast.Starred)) for a in call_node.args):
        return False
    return all(isinstance(k.value, ast.Name) for k in call_node.keywords)


def check_pass_through_methods(tree: ast.Module, filepath: Path, pkg: str) -> list[Issue]:
    """Detect methods that do nothing but delegate to another function."""
    issues: list[Issue] = []

    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if node.name.startswith("__") and node.name.endswith("__"):
            continue

        body = _non_docstring_body(node)
        if len(body) != 1:
            continue

        call_node = _call_from_stmt(body[0])
        if (
            call_node is None
            or _is_super_call(call_node)
            or not _is_pure_delegation_call(call_node)
        ):
            continue

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
    check_magic_strings,
    check_missing_else,
    check_lazy_class,
    check_deep_inheritance,
    check_pass_through_methods,
]
