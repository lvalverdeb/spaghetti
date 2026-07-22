"""Per-file AST-based checks — the core 27 rules."""

from __future__ import annotations

import ast
from collections import defaultdict
from collections.abc import Callable, Iterator
from pathlib import Path

from spaghetti.ast_helpers import (
    _count_nested_returns,
    _cyclomatic_complexity,
    _dump_stmts,
    _has_return_type_hint,
    _is_private,
    _is_test_file,
    _is_test_function,
    _line_count,
    _nesting_depth,
    _param_has_type_hint,
)
from spaghetti.config import (
    COMPLEXITY_THRESHOLD,
    ERROR_ESCALATION_MULTIPLIER,
    LAYER_RULES,
    LONG_FUNCTION_FLAT_NESTING_MAX,
    MAX_CLASS_ATTRS,
    MAX_CLASS_LCOM4,
    MAX_CLASS_METHODS,
    MAX_CLASS_WMC,
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
    MIN_METHODS_FOR_COHESION,
    MIN_STATEFUL_METHOD_FRACTION,
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
            if lines > MAX_FUNCTION_LINES and _nesting_depth(node) > LONG_FUNCTION_FLAT_NESTING_MAX:
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
            if _is_private(node.name) or _is_test_function(node.name) or _is_test_file(filepath):
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
            n_returns = _count_nested_returns(node)
            if n_returns > MAX_RETURNS:
                issues.append(
                    Issue(
                        file=filepath,
                        line=node.lineno,
                        severity="info",
                        rule="excessive-returns",
                        package=pkg,
                        message=(
                            f"{node.name}() has {n_returns} nested return statements "
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


def _first_param_name(func: ast.FunctionDef | ast.AsyncFunctionDef) -> str | None:
    params = func.args.posonlyargs + func.args.args
    return params[0].arg if params else None


def _walk_with_encapsulation_context(
    tree: ast.AST,
) -> Iterator[tuple[ast.AST, str | None, str | None]]:
    """Yield (node, enclosing_class_name_or_None, enclosing_function's first
    positional param name_or_None) for every node in the tree.

    A local variant of ``_walk_with_class_context`` (which only tracks the
    class) because check_encapsulation_violations also needs to recognize a
    free function's own explicit "self-like" first parameter — see
    ``_is_allowed_private_access_base``.
    """

    def visit(
        node: ast.AST, class_name: str | None, first_param: str | None
    ) -> Iterator[tuple[ast.AST, str | None, str | None]]:
        for child in ast.iter_child_nodes(node):
            new_class_name = class_name
            new_first_param = first_param
            if isinstance(child, ast.ClassDef):
                new_class_name = child.name
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                new_first_param = _first_param_name(child)
            yield child, class_name, first_param
            yield from visit(child, new_class_name, new_first_param)

    yield from visit(tree, None, None)


def _is_allowed_private_access_base(
    base: ast.AST, class_name: str | None, first_param_name: str | None
) -> bool:
    """True if *base* is ``self``/``cls``/the enclosing class/``super()``, or
    the enclosing function's own first parameter — i.e. a private member
    reached through it is *not* an encapsulation violation.

    The first-parameter case covers module-level free functions that take a
    not-yet-fully-constructed (or otherwise "owning") instance explicitly as
    their first argument instead of being a method — a documented pattern in
    this codebase for splitting a class's own ``__init__``/method bodies out
    for line-count headroom (see e.g. ``boti_data.gateway._gateway_init``).
    Reaching into that parameter's private state is the free-function
    equivalent of ``self`` access, not a real violation. This is syntactic,
    not semantic: it can't tell "the extracted-method pattern" from "a
    function that just happens to take some other object as its first
    argument and reaches into it for no good reason" — but the former is
    common and idiomatic enough here that the trade-off favors exempting it.
    """
    if isinstance(base, ast.Name) and base.id in ("self", "cls", class_name, first_param_name):
        return True
    return (
        isinstance(base, ast.Call) and isinstance(base.func, ast.Name) and base.func.id == "super"
    )


def _is_private_name(name: str) -> bool:
    return name.startswith("_") and not _DUNDER_RE.match(name)


def _direct_private_access(
    node: ast.AST, class_name: str | None, first_param_name: str | None
) -> str | None:
    """The private attribute name reached via ``obj._attr``, or None."""
    if not (isinstance(node, ast.Attribute) and isinstance(node.ctx, ast.Load)):
        return None
    if not _is_private_name(node.attr) or _is_allowed_private_access_base(
        node.value, class_name, first_param_name
    ):
        return None
    return node.attr


def _reflective_private_access(
    node: ast.AST, class_name: str | None, first_param_name: str | None
) -> tuple[str, str] | None:
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
        node.args[0], class_name, first_param_name
    ):
        return None
    return node.func.id, attr_arg.value


def check_encapsulation_violations(tree: ast.Module, filepath: Path, pkg: str) -> list[Issue]:
    """Reaching into another object's private (``_name``) attribute from outside
    self/cls/its own class."""
    issues: list[Issue] = []

    for node, class_name, first_param_name in _walk_with_encapsulation_context(tree):
        direct_attr = _direct_private_access(node, class_name, first_param_name)
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

        reflective = _reflective_private_access(node, class_name, first_param_name)
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
    """A class with too many methods or instance attributes, or too much total
    complexity across its methods (WMC — Weighted Methods per Class) even if
    the method/attribute counts alone stay under threshold."""
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
        wmc = sum(_cyclomatic_complexity(method) for method in methods)
        if len(methods) > MAX_CLASS_METHODS or len(attrs) > MAX_CLASS_ATTRS or wmc > MAX_CLASS_WMC:
            severity = (
                "error"
                if len(methods) > MAX_CLASS_METHODS * ERROR_ESCALATION_MULTIPLIER
                or len(attrs) > MAX_CLASS_ATTRS * ERROR_ESCALATION_MULTIPLIER
                or wmc > MAX_CLASS_WMC * ERROR_ESCALATION_MULTIPLIER
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
                        f"class {node.name} has {len(methods)} methods, {len(attrs)} attributes, "
                        f"and total complexity (WMC) {wmc} (max {MAX_CLASS_METHODS}/"
                        f"{MAX_CLASS_ATTRS}/{MAX_CLASS_WMC}) — consider splitting responsibilities"
                    ),
                )
            )
    return issues


# ── Rule: Low Cohesion ────────────────────────────────────────────────────────


_LCOM_EXEMPT_DECORATOR_NAMES = frozenset({"classmethod", "staticmethod"})


def _is_instance_method(method: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    """False for ``@classmethod``/``@staticmethod`` — they operate on ``cls``
    or nothing at all, never ``self``, so they can't meaningfully connect to
    (or split from) the rest of a class's cohesion graph. Including them
    would flag every Pydantic model with a couple of ``@classmethod`` factory
    methods or ``@field_validator``s as incohesive, regardless of its actual
    instance-method cohesion — a category error, not a real signal.
    """
    for dec in method.decorator_list:
        target = dec.func if isinstance(dec, ast.Call) else dec
        name = target.id if isinstance(target, ast.Name) else getattr(target, "attr", None)
        if name in _LCOM_EXEMPT_DECORATOR_NAMES:
            return False
    return True


def _method_self_attrs(method: ast.FunctionDef | ast.AsyncFunctionDef) -> set[str]:
    """Every ``self.<attr>`` *data field* *method* references, read or write
    — excludes ``self.<name>(...)`` method calls, which are an invocation
    relationship (this method calls that one), not shared instance state.
    Real LCOM4 definitions specifically track field access rather than any
    attribute reference for exactly this reason: two methods that each call
    a different private helper aren't "connected" by shared state just
    because both happen to reference *some* attribute of ``self``.
    """
    call_targets = {id(sub.func) for sub in ast.walk(method) if isinstance(sub, ast.Call)}
    attrs: set[str] = set()
    for sub in ast.walk(method):
        if (
            isinstance(sub, ast.Attribute)
            and isinstance(sub.value, ast.Name)
            and sub.value.id == "self"
            and id(sub) not in call_targets
        ):
            attrs.add(sub.attr)
    return attrs


def _count_disjoint_clusters(attr_sets: list[set[str]]) -> int:
    """LCOM4: the number of connected components in the graph where two
    methods (by index into *attr_sets*) are linked if their ``self.<attr>``
    sets intersect — plain union-find, no graph library needed.

    A method that touches no ``self.*`` state at all (e.g. a pure helper that
    only calls other methods, or a method operating purely on its own
    parameters) forms its own singleton component here, since this only
    links methods through *shared attributes*, not method-to-method calls —
    the same simplification the reference implementation this was ported
    from (SMART-Dal's ``smell-detector-python``) makes.
    """
    n = len(attr_sets)
    parent = list(range(n))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def union(i: int, j: int) -> None:
        root_i, root_j = find(i), find(j)
        if root_i != root_j:
            parent[root_i] = root_j

    for i in range(n):
        for j in range(i + 1, n):
            if attr_sets[i] & attr_sets[j]:
                union(i, j)

    return len({find(i) for i in range(n)})


_INTERFACE_BASE_NAMES = frozenset({"ABC", "Protocol"})


def _is_interface_class(node: ast.ClassDef) -> bool:
    """True if *node* directly subclasses ``ABC``/``(typing.)Protocol``, or
    uses ``metaclass=ABCMeta`` — a pure interface declaration, deliberately
    stateless by design (see e.g. ``boti_data.gateway.BackendStrategy``:
    "Subclasses are stateless"). LCOM4 has nothing meaningful to measure when
    there's no shared state to begin with.

    This only catches the interface declaration itself, not a *concrete*
    class implementing a broad interface defined in another file — that
    would need cross-file type/hierarchy resolution this per-file check
    doesn't have. A concrete Strategy-pattern implementation genuinely can
    still read as artificially low-cohesion here; known limitation, not
    fixed by this exemption.
    """
    for base in node.bases:
        name = base.id if isinstance(base, ast.Name) else getattr(base, "attr", None)
        if name in _INTERFACE_BASE_NAMES:
            return True
    for kw in node.keywords:
        if kw.arg == "metaclass":
            name = (
                kw.value.id if isinstance(kw.value, ast.Name) else getattr(kw.value, "attr", None)
            )
            if name == "ABCMeta":
                return True
    return False


def check_low_cohesion(tree: ast.Module, filepath: Path, pkg: str) -> list[Issue]:
    """Flag classes whose methods split into disjoint clusters sharing no
    ``self.*`` state (LCOM4 — Lack of Cohesion in Methods).

    LCOM4 of 1 means fully cohesive: every method connects to every other
    through shared state, directly or transitively. Anything higher means
    the class is really two or more unrelated classes glued together.
    ``@classmethod``/``@staticmethod`` methods are excluded entirely (see
    ``_is_instance_method``), and classes under ``MIN_METHODS_FOR_COHESION``
    *instance* methods are skipped — too little surface to meaningfully split.

    Also skipped for classes already exempt from ``lazy-class`` (Pydantic
    ``BaseModel``/``BaseSettings``, ``@dataclass``, ``NamedTuple``, exception
    subclasses — see ``_lazy_class_is_exempt``): a declarative data container
    has no explicit ``__init__`` body assigning all fields together to anchor
    cohesion, so a handful of hand-written methods each touching only the
    field(s) relevant to their own behavior — completely idiomatic for a
    small value-object/dataclass hierarchy — reads as an artificial LCOM4
    split that isn't a real "doing too many unrelated things" smell.

    Pure pass-through methods (see ``_is_pure_pass_through``) are excluded
    from the method set the same way ``@classmethod``/``@staticmethod`` are:
    a Facade class whose methods mostly delegate to another object or a free
    function (``def load(self, **o): return core_load.load_sync(self, o)``)
    never touches ``self.<attr>`` in those methods, which would otherwise
    make every one of them its own disconnected cluster regardless of how
    cohesive the class actually is.

    Also skipped for interface declarations (see ``_is_interface_class``):
    deliberately stateless by design, so LCOM4 has nothing to measure. The
    same reasoning applies more generally: a class where fewer than
    ``MIN_STATEFUL_METHOD_FRACTION`` of its methods reference *any*
    ``self.<attr>`` data field — an essentially stateless Strategy/Handler-
    pattern implementation, common in this codebase (see e.g.
    ``boti_data.gateway.sql_strategy.SqlAlchemyStrategy``, which implements
    a broad ``BackendStrategy`` interface with almost no instance state) —
    has too little state for LCOM4 to meaningfully (dis)connect; most
    methods would otherwise become their own cluster purely because there's
    no state to share, which measures "this class holds little data" rather
    than "this class does unrelated things with the data it holds". A
    fractional threshold (not an all-or-nothing "zero attrs" check) also
    covers classes that are almost entirely stateless but for a method or
    two referencing a sibling method as a bare callable rather than calling
    it directly (e.g. ``asyncio.to_thread(self.other_method, ...)``), which
    still isn't real field access.
    """
    issues: list[Issue] = []
    for node in ast.walk(tree):
        if (
            not isinstance(node, ast.ClassDef)
            or _lazy_class_is_exempt(node)
            or _is_interface_class(node)
        ):
            continue
        methods = [
            n
            for n in node.body
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
            and _is_instance_method(n)
            and not _is_pure_pass_through(n)
        ]
        if len(methods) < MIN_METHODS_FOR_COHESION:
            continue

        attr_sets = [_method_self_attrs(method) for method in methods]
        stateful_fraction = sum(1 for s in attr_sets if s) / len(attr_sets)
        if stateful_fraction < MIN_STATEFUL_METHOD_FRACTION:
            continue
        lcom4 = _count_disjoint_clusters(attr_sets)
        if lcom4 > MAX_CLASS_LCOM4:
            issues.append(
                Issue(
                    file=filepath,
                    line=node.lineno,
                    severity="warning",
                    rule="low-cohesion",
                    package=pkg,
                    message=(
                        f"class {node.name} has LCOM4={lcom4} ({lcom4} method clusters "
                        "sharing no self.* state with each other) — consider splitting "
                        "into cohesive classes"
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


def _is_none_guard_test(test: ast.expr, target_name: str) -> bool:
    """True for `<target_name> is None`."""
    return (
        isinstance(test, ast.Compare)
        and isinstance(test.left, ast.Name)
        and test.left.id == target_name
        and len(test.ops) == 1
        and isinstance(test.ops[0], ast.Is)
        and len(test.comparators) == 1
        and isinstance(test.comparators[0], ast.Constant)
        and test.comparators[0].value is None
    )


def _is_lazy_singleton_init(func_node: ast.AST, target_name: str, mutation: ast.AST) -> bool:
    """True if *mutation* sits directly inside an ``if <target_name> is
    None:`` guard — the standard lazy-singleton-init idiom (load an
    expensive resource once, cache it in a module-level variable initialized
    to ``None``). This has exactly one writer, one purpose, and no ordering
    dependency — unlike unconstrained global mutation, which is what this
    rule otherwise exists to catch.
    """
    return any(
        isinstance(node, ast.If)
        and _is_none_guard_test(node.test, target_name)
        and mutation in node.body
        for node in _walk_own_scope(func_node)
    )


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

            if _is_lazy_singleton_init(func_node, target_name, child):
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


def _chain_root_name(node: ast.expr) -> str | None:
    """The bare name a chain bottoms out at, e.g. ``PostgresContainer`` for
    ``PostgresContainer(...).with_bind_ports(...).start()`` — None if the
    chain bottoms out at anything else (a Subscript, a Constant, ...)."""
    if isinstance(node, ast.Call):
        return _chain_root_name(node.func)
    if isinstance(node, ast.Attribute):
        return _chain_root_name(node.value)
    if isinstance(node, ast.Name):
        return node.id
    return None


def check_message_chains(tree: ast.Module, filepath: Path, pkg: str) -> list[Issue]:
    """Flag method / attribute chains deeper than MAX_MESSAGE_CHAIN_DEPTH.

    Exempts chains rooted directly in an imported name (e.g. a fluent
    builder chained straight off a call to an imported class, like
    testcontainers' ``PostgresContainer(...).with_bind_ports(...).start()``)
    — that shape reflects a third-party library's own API, not a coupling
    problem in this codebase's object graph. Doesn't do full dataflow
    tracking: a chain rooted in a local variable previously assigned from an
    imported call isn't recognized, only one whose root expression is the
    import itself within the same expression.
    """
    issues: list[Issue] = []
    imported = _collect_imported_names(tree)

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
                    root = _chain_root_name(child)
                    if root is not None and root in imported:
                        continue
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


def _magic_number_display(source: str, node: ast.Constant) -> str:
    """The literal exactly as written in *source* (``0o600``, ``0x1F``,
    ``1_000``), not the decimal value of its parsed ``ast.Constant.value`` —
    the AST itself has no notation to fall back on, since e.g. ``0o600`` and
    ``384`` parse to the identical int. Reporting "magic number 384" for a
    literal someone wrote as ``0o600`` reads as arbitrary when it isn't."""
    return ast.get_source_segment(source, node) or repr(node.value)


# Standard HTTP status codes and well-known infra ports: as conventionally
# idiomatic as -1/0/1 (every reader recognizes 404 or 5432 instantly; naming
# them buys nothing over the literal), so allowed unconditionally rather than
# only when compared against e.g. `.status_code` — matching how -1/0/1 are
# already allowed everywhere, not just in specific contexts.
_MAGIC_NUMBER_HTTP_STATUS_CODES = {
    100, 101, 200, 201, 202, 204, 301, 302, 304,
    400, 401, 403, 404, 405, 409, 410, 422, 429,
    500, 502, 503, 504,
}  # fmt: skip
_MAGIC_NUMBER_WELL_KNOWN_PORTS = {80, 443, 3306, 5432, 6379, 8080, 8200, 27017}

# A file with this many *distinct* disallowed numeric literals reads as an
# intentional parameter/threshold table (e.g. an FMR/FNMR bake-off script's
# tuning constants) rather than incidental magic numbers creeping into
# control flow — "many distinct numbers with no repeats" is itself the
# signal, not any one value. Comfortably above what an ordinary file
# accumulates, comfortably below a genuinely parametric script's count.
_MAGIC_NUMBER_DENSE_FILE_MIN_DISTINCT = 8


def check_magic_numbers(tree: ast.Module, source: str, filepath: Path, pkg: str) -> list[Issue]:
    """Flag numeric literals other than 0, 1, or -1 in a function's body.

    Skips the function's own signature — a default parameter value (e.g.
    ``base_delay: float = 0.5``) is already named by the parameter itself —
    and skips a literal passed as a call's keyword argument (e.g.
    ``stacklevel=2``), since the keyword name documents it the same way a
    named constant would. ``__init__`` methods are skipped entirely. Also
    allows standard HTTP status codes and well-known infra ports (see
    ``_MAGIC_NUMBER_HTTP_STATUS_CODES``/``_MAGIC_NUMBER_WELL_KNOWN_PORTS``),
    and skips a file entirely once it accumulates enough distinct magic
    numbers to read as a parameter table rather than a smell (see
    ``_MAGIC_NUMBER_DENSE_FILE_MIN_DISTINCT``).

    Not part of ``ALL_CHECKS``/``FileCheck``: unlike every other per-file
    check, this one needs the raw source text (see ``_magic_number_display``)
    in addition to the parsed tree, so ``detector.py`` calls it directly.
    """
    _ALLOWED = {-1, 0, 1} | _MAGIC_NUMBER_HTTP_STATUS_CODES | _MAGIC_NUMBER_WELL_KNOWN_PORTS
    candidates: list[tuple[ast.Constant, str]] = []

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
                candidates.append((child, _magic_number_display(source, child)))

    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            _scan_func(node)

    distinct = {display for _, display in candidates}
    if len(distinct) >= _MAGIC_NUMBER_DENSE_FILE_MIN_DISTINCT:
        return []

    return [
        Issue(
            file=filepath,
            line=child.lineno,
            severity="info",
            rule="magic-number",
            package=pkg,
            message=(
                f"magic number {display} — extract to a named constant, or an "
                "enum.IntEnum if it's one of a fixed set of status/category codes"
            ),
        )
        for child, display in candidates
    ]


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

    Skipped entirely for test files: a value compared twice in one test
    function is normal arrange-then-assert structure, not a sign of a
    missing domain concept.
    """
    if _is_test_file(filepath):
        return []

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

# Assignment target types that mean the ``if`` body's trailing assignment is
# mutating state that already exists outside the branch (an attribute, a
# dict/list slot) rather than introducing a value that only this branch
# knows about. ``ast.Name`` is deliberately excluded: a fresh local (`a = 1`)
# is exactly the shape that might need a counterpart in a missing negative
# path, so it stays flagged — see check_missing_else's docstring.
_STATE_MUTATION_TARGET_TYPES = (ast.Attribute, ast.Subscript)


def _assignment_targets(stmt: ast.stmt) -> list[ast.expr]:
    if isinstance(stmt, ast.Assign):
        return stmt.targets
    if isinstance(stmt, (ast.AugAssign, ast.AnnAssign)):
        return [stmt.target]
    return []


def _is_trailing_state_mutation(stmt: ast.stmt) -> bool:
    targets = _assignment_targets(stmt)
    return bool(targets) and all(isinstance(t, _STATE_MUTATION_TARGET_TYPES) for t in targets)


def check_missing_else(tree: ast.Module, filepath: Path, pkg: str) -> list[Issue]:
    """Flag ``if`` blocks with 2+ statements but no ``else``/``elif``.

    Skipped when the ``if`` body's last statement already terminates control
    flow (``return``/``raise``/``continue``/``break``): the negative path is
    either "the rest of the function" or "the next loop iteration", and is
    not missing.

    Also skipped when the body's last statement is itself a bare ``if``
    (no ``elif``/``else`` of its own), a discarded-return call expression
    (e.g. ``issues.append(...)``) or bare ``yield``/``yield from``, or a
    ``for``/``while`` loop. All these shapes — some setup then a single
    trailing conditional, side-effect call, yield, or iteration — mean the
    ``if`` exists only to *guard entry* into that final step (e.g. "if this
    is the right node type: compute X, then record it" / "...then check
    each of its sub-elements"), not to encode two real branches of logic.
    The "negative path" is just "skip this node", which is already what
    happens without an else.

    Also skipped when the body's last statement assigns to an attribute or
    subscript (``self.x = ...`` / ``cache[key] = ...``) rather than a fresh
    local name: this is the "conditionally update already-existing state"
    idiom (lazy-init-then-cache, one-time setup flags, degrade-in-place
    dicts) where the "negative path" is simply "leave the existing value
    alone" — already true without an else. A fresh local name (``a = 1``)
    is not exempted: unlike an attribute/subscript, it has no existence
    outside the branch, so it's the shape most likely to actually be
    missing its negative-path counterpart.
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
        if isinstance(node.body[-1], ast.Expr) and isinstance(
            node.body[-1].value, (ast.Call, ast.Yield, ast.YieldFrom)
        ):
            continue
        if _is_trailing_state_mutation(node.body[-1]):
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
# nonsensical since they already fulfill that exact role. Protocol/Enum
# variants are a different reason for the same exemption: a structural-
# typing interface's whole point is a low method count, and an enum's
# members aren't methods at all — neither can be expressed as a dataclass
# or plain function either.
_LAZY_CLASS_EXEMPT_BASE_NAMES = frozenset(
    {
        "BaseModel",
        "BaseSettings",
        "NamedTuple",
        "Protocol",
        "Enum",
        "StrEnum",
        "IntEnum",
        "IntFlag",
        "Flag",
    }
)

# A base class named by the standard exception/warning naming convention
# (PEP 8: "exception names should use the CapWords convention and the
# suffix Error/Exception/Warning") makes a class raise-able — it can't be
# "a plain function or @dataclass" and still be an exception type. Matching
# by suffix (rather than a fixed list of builtins) also covers subclassing
# a project's own custom exception base, not just direct builtin bases.
_LAZY_CLASS_EXEMPT_BASE_SUFFIXES = ("Error", "Exception", "Warning")


def _lazy_class_decorator_target_name(dec: ast.expr) -> str | None:
    """The name a decorator resolves to, e.g. 'dataclass' for both
    ``@dataclass`` and ``@dataclass(frozen=True)``."""
    target = dec.func if isinstance(dec, ast.Call) else dec
    if isinstance(target, ast.Name):
        return target.id
    if isinstance(target, ast.Attribute):
        return target.attr
    return None


def _lazy_class_attr_target_name(node: ast.expr) -> str | None:
    """The bare name a `Mapped[...]`/`mapped_column(...)`/`Column(...)`
    reference resolves to, whether written as `Mapped`/`mapped_column`/
    `Column` directly or qualified (`orm.Mapped`, `sa.Column`)."""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


def _is_mapped_annotation(annotation: ast.expr) -> bool:
    """True for a `Mapped[...]` annotation (SQLAlchemy 2.0 typed declarative)."""
    target = annotation.value if isinstance(annotation, ast.Subscript) else annotation
    return _lazy_class_attr_target_name(target) == "Mapped"


def _is_orm_column_call(value: ast.expr | None) -> bool:
    """True for a `mapped_column(...)`/`Column(...)` call (SQLAlchemy 2.0
    untyped declarative, or legacy 1.x Declarative style)."""
    if not isinstance(value, ast.Call):
        return False
    return _lazy_class_attr_target_name(value.func) in {"mapped_column", "Column"}


def _lazy_class_has_orm_columns(node: ast.ClassDef) -> bool:
    """True if *node* has at least one SQLAlchemy declarative column
    attribute. This is what actually makes a class an ORM model — a
    project's own declarative ``Base`` can be named anything, so matching by
    base-class name (like the other exemptions below) would be guesswork;
    the column declarations are the unambiguous signal.
    """
    for stmt in node.body:
        if isinstance(stmt, ast.AnnAssign) and (
            _is_mapped_annotation(stmt.annotation) or _is_orm_column_call(stmt.value)
        ):
            return True
        if isinstance(stmt, ast.Assign) and _is_orm_column_call(stmt.value):
            return True
    return False


def _lazy_class_is_exempt(node: ast.ClassDef) -> bool:
    """True if *node* already is a declarative data container, or a raise-able
    exception/warning type.

    Pydantic ``BaseModel``/``BaseSettings`` subclasses, SQLAlchemy
    declarative models (see ``_lazy_class_has_orm_columns``), and
    ``@dataclass``-decorated classes already satisfy check_lazy_class's own
    suggested remedy, so they should never be flagged regardless of method
    count. Exception/warning subclasses are exempt for a different reason:
    the remedy itself (a plain function or dataclass) isn't raise-able, so it
    doesn't apply.
    """
    if _lazy_class_has_orm_columns(node):
        return True
    for base in node.bases:
        name = base.id if isinstance(base, ast.Name) else getattr(base, "attr", None)
        if name is None:
            continue
        if name in _LAZY_CLASS_EXEMPT_BASE_NAMES or name.endswith(_LAZY_CLASS_EXEMPT_BASE_SUFFIXES):
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
        if (
            isinstance(node, ast.ClassDef)
            and not node.name.startswith("_")
            and not _lazy_class_is_exempt(node)
        ):
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


def _is_pure_pass_through(node: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    """True if *node*'s body is nothing but a delegating call — the same
    shape check_pass_through_methods flags, exposed as a predicate (rather
    than an Issue) for check_low_cohesion to reuse: a pure pass-through
    method (e.g. ``def load(self, **o): return core_load.load_sync(self,
    o)``) routes to a free function instead of touching ``self.<attr>``
    directly, which is the same "extracted method takes the instance
    explicitly" pattern already exempted in encapsulation-violation — not a
    real cohesion signal either way, just routing.
    """
    body = _non_docstring_body(node)
    if len(body) != 1:
        return False
    call_node = _call_from_stmt(body[0])
    return (
        call_node is not None
        and not _is_super_call(call_node)
        and _is_pure_delegation_call(call_node)
    )


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

# check_low_cohesion is deliberately NOT included below. Its LCOM4
# computation is correct and well-tested (see the low_cohesion_tests /
# test_check_low_cohesion_* suite), but on this codebase's dominant style —
# small, focused private methods taking explicit parameters rather than
# touching self, minimal shared mutable state by design — it still produces
# real remaining noise that direct-field-sharing LCOM4 can't distinguish
# from genuine low cohesion without transitive call-graph reachability (a
# materially bigger feature, not a quick fix). Held back rather than shipped
# noisy; revisit if/when that reachability analysis is worth building.
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
    check_magic_strings,
    check_missing_else,
    check_lazy_class,
    check_deep_inheritance,
    check_pass_through_methods,
]
