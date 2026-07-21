"""Cross-file, whole-package checks (import cycles, duplicate functions, sync/async twins)."""

from __future__ import annotations

import ast
import difflib
from collections import defaultdict
from pathlib import Path

from spaghetti.ast_helpers import (
    _dump_stmts,
    _is_trivial_body,
    _line_count,
    _walk_with_class_context,
)
from spaghetti.config import MAX_MODULE_FAN_IN, MAX_MODULE_FAN_OUT, MIN_TWIN_FUNCTION_LINES
from spaghetti.models import Issue, _display_path

__all__ = [
    "check_import_cycles_pkg",
    "check_duplicate_functions_pkg",
    "check_module_coupling_pkg",
    "check_sync_async_twins_pkg",
]


# ── Import Cycles (real cycle detection via DFS) ─────────────────────────────


def _module_and_package_for(pkg_name: str, filepath: Path) -> tuple[str, str]:
    """Returns (dotted_module_name, dotted_containing_package_name)."""
    # Late import so tests can monkeypatch detector.PACKAGES
    import spaghetti.detector as _det

    pkg_root = _det.PACKAGES[pkg_name]
    rel = filepath.relative_to(pkg_root.parent)
    parts = list(rel.with_suffix("").parts)
    if parts[-1] == "__init__":
        module = ".".join(parts[:-1])
        return module, module
    module = ".".join(parts)
    return module, ".".join(parts[:-1])


def _import_targets(package: str, node: ast.ImportFrom) -> list[str]:
    """Resolves an ImportFrom to the dotted module name(s) it depends on."""
    if node.level == 0:
        return [node.module] if node.module else []
    pkg_parts = package.split(".") if package else []
    up = node.level - 1
    if up:
        pkg_parts = pkg_parts[: len(pkg_parts) - up] if up <= len(pkg_parts) else []
    base = ".".join(pkg_parts)
    if node.module:
        return [f"{base}.{node.module}" if base else node.module]
    return [f"{base}.{alias.name}" if base else alias.name for alias in node.names]


def _package_prefix(pkg_name: str) -> str:
    # Late import so tests can monkeypatch detector.ALLOWED_IMPORT_PREFIXES
    import spaghetti.detector as _det

    prefixes = _det.ALLOWED_IMPORT_PREFIXES.get(pkg_name, [])
    return prefixes[0] if prefixes else ""


def _is_type_checking_guard(node: ast.If) -> bool:
    test = node.test
    if isinstance(test, ast.Name) and test.id == "TYPE_CHECKING":
        return True
    return isinstance(test, ast.Attribute) and test.attr == "TYPE_CHECKING"


def _module_level_imports(tree: ast.Module) -> list[ast.Import | ast.ImportFrom]:
    """Imports that actually execute at module-import time.

    Excludes imports nested inside a function/method body and
    ``if TYPE_CHECKING:`` blocks.
    """
    found: list[ast.Import | ast.ImportFrom] = []

    def visit(node: ast.AST) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                continue
            if isinstance(child, ast.If) and _is_type_checking_guard(child):
                continue
            if isinstance(child, (ast.Import, ast.ImportFrom)):
                found.append(child)
            visit(child)

    visit(tree)
    return found


def _build_import_graph(
    pkg_name: str, files: list[tuple[Path, ast.Module]]
) -> tuple[dict[str, set[str]], dict[str, Path]]:
    """The real intra-package import graph: module -> set of modules it
    imports, plus module -> its file. Shared by check_import_cycles_pkg and
    check_module_coupling_pkg so both see identical import resolution
    (relative imports, package-prefix resolution, TYPE_CHECKING exclusion)
    instead of duplicating it.

    A module only appears as a ``graph`` key if it has at least one
    qualifying import; a module with zero intra-package imports is still
    present in ``file_for_module``, just absent from ``graph`` (equivalent
    to an empty adjacency set — callers use ``graph.get(module, ...)``).
    """
    prefix = _package_prefix(pkg_name)
    graph: dict[str, set[str]] = defaultdict(set)
    file_for_module: dict[str, Path] = {}
    if not prefix:
        return graph, file_for_module
    stem = prefix.rstrip(".")

    for filepath, tree in files:
        module, package = _module_and_package_for(pkg_name, filepath)
        file_for_module[module] = filepath
        for node in _module_level_imports(tree):
            if isinstance(node, ast.ImportFrom):
                for resolved in _import_targets(package, node):
                    if resolved and (resolved == stem or resolved.startswith(stem + ".")):
                        graph[module].add(resolved)
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name == stem or alias.name.startswith(stem + "."):
                        graph[module].add(alias.name)

    return graph, file_for_module


def check_import_cycles_pkg(pkg_name: str, files: list[tuple[Path, ast.Module]]) -> list[Issue]:
    """Finds actual import cycles via DFS over the real intra-package import graph."""
    graph, file_for_module = _build_import_graph(pkg_name, files)
    if not file_for_module:
        return []

    issues: list[Issue] = []
    visited: set[str] = set()
    on_stack: set[str] = set()
    stack: list[str] = []
    reported: set[frozenset[str]] = set()

    def dfs(current: str) -> None:
        visited.add(current)
        stack.append(current)
        on_stack.add(current)
        for neighbor in graph.get(current, ()):
            if neighbor in on_stack:
                cycle_start = stack.index(neighbor)
                cycle = [*stack[cycle_start:], neighbor]
                key = frozenset(cycle)
                if key not in reported:
                    reported.add(key)
                    loc = file_for_module.get(stack[cycle_start])
                    if loc is not None:
                        issues.append(
                            Issue(
                                file=loc,
                                line=1,
                                severity="error",
                                rule="import-cycle",
                                package=pkg_name,
                                message=(
                                    "Circular import: "
                                    + " → ".join(cycle)
                                    + " — extract a shared abstraction (e.g. a "
                                    "typing.Protocol) both sides can depend on, and inject "
                                    "it instead of importing directly to break the cycle "
                                    "(Dependency Inversion Principle / Dependency Injection)"
                                ),
                            )
                        )
            elif neighbor not in visited:
                dfs(neighbor)
        stack.pop()
        on_stack.discard(current)

    for module in list(graph.keys()):
        if module not in visited:
            dfs(module)

    return issues


def check_module_coupling_pkg(pkg_name: str, files: list[tuple[Path, ast.Module]]) -> list[Issue]:
    """Flags a module as an overloaded "hub" — reusing the same import graph
    as check_import_cycles_pkg, but measuring fan-in/fan-out instead of
    cycles.

    Only flagged when *both* fan-in and fan-out exceed their thresholds:
    high fan-in alone is often just a legitimately central util module (many
    things use it, by design), and high fan-out alone is often just a
    legitimately thin orchestrator (it wires many things together, by
    design). Both-high together is the real signal — a module that's both
    heavily depended-on and heavily dependent, so a change anywhere near it
    tends to ripple.
    """
    graph, file_for_module = _build_import_graph(pkg_name, files)
    if not file_for_module:
        return []

    fan_in: dict[str, int] = defaultdict(int)
    for imports in graph.values():
        for target in imports:
            fan_in[target] += 1

    issues: list[Issue] = []
    for module, filepath in file_for_module.items():
        module_fan_out = len(graph.get(module, ()))
        module_fan_in = fan_in.get(module, 0)
        if module_fan_in > MAX_MODULE_FAN_IN and module_fan_out > MAX_MODULE_FAN_OUT:
            issues.append(
                Issue(
                    file=filepath,
                    line=1,
                    severity="warning",
                    rule="high-coupling",
                    package=pkg_name,
                    message=(
                        f"module {module} has fan-in={module_fan_in} and "
                        f"fan-out={module_fan_out} (max {MAX_MODULE_FAN_IN}/{MAX_MODULE_FAN_OUT}) "
                        "— other modules depend on it heavily and it depends on other "
                        "modules heavily; consider splitting it or inverting some "
                        "dependencies (Dependency Inversion Principle)"
                    ),
                )
            )
    return issues


# ── Duplicate Function Bodies (whole-package) ─────────────────────────────────

# A body needs at least this many occurrences to be a "duplicate" at all.
_MIN_DUPLICATE_OCCURRENCES = 2


def check_duplicate_functions_pkg(
    pkg_name: str, files: list[tuple[Path, ast.Module]], min_lines: int
) -> list[Issue]:
    """Finds functions/methods with byte-for-byte identical bodies anywhere in
    the package."""
    groups: dict[str, list[tuple[Path, ast.FunctionDef | ast.AsyncFunctionDef]]] = defaultdict(list)

    for filepath, tree in files:
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if _line_count(node) < min_lines or _is_trivial_body(node):
                continue
            groups[_dump_stmts(node.body)].append((filepath, node))

    issues: list[Issue] = []
    for occurrences in groups.values():
        if len(occurrences) < _MIN_DUPLICATE_OCCURRENCES:
            continue
        occurrences.sort(key=lambda item: (str(item[0]), item[1].lineno))
        locations = ", ".join(f"{_display_path(fp)}:{n.lineno} ({n.name})" for fp, n in occurrences)
        first_fp, first_node = occurrences[0]
        issues.append(
            Issue(
                file=first_fp,
                line=first_node.lineno,
                severity="warning",
                rule="duplicate-function-body",
                package=pkg_name,
                message=f"Identical body in {len(occurrences)} places: {locations}",
            )
        )
    return issues


# ── Sync/Async Twin Duplication ───────────────────────────────────────────────


def _twin_candidates(name: str) -> set[str]:
    """Plausible sync/async counterpart names for ``name``."""
    candidates = {f"a{name}", f"{name}_async", f"async_{name}", f"_async_{name}"}
    if name.startswith("a") and len(name) > 1:
        candidates.add(name[1:])
    if name.startswith("_async_"):
        candidates.add(name[len("_async_") :])
    if name.endswith("_async"):
        candidates.add(name[: -len("_async")])
    if name.startswith("async_"):
        candidates.add(name[len("async_") :])
    if name.endswith("_sync"):
        stripped = name[: -len("_sync")]
        candidates.add(stripped)
        candidates.add(f"{stripped}_async")
    return candidates


def check_sync_async_twins_pkg(
    pkg_name: str, files: list[tuple[Path, ast.Module]], min_ratio: float
) -> list[Issue]:
    """Flags function pairs that look like a sync/async "twin" (by name) and
    whose bodies are highly similar text."""
    scope_functions: dict[
        tuple[Path, str | None], dict[str, tuple[ast.FunctionDef | ast.AsyncFunctionDef, bool]]
    ] = defaultdict(dict)

    for filepath, tree in files:
        for node, class_name in _walk_with_class_context(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                scope_functions[(filepath, class_name)][node.name] = (
                    node,
                    isinstance(node, ast.AsyncFunctionDef),
                )

    issues: list[Issue] = []
    reported: set[frozenset[tuple[str, str]]] = set()

    for (filepath, class_name), by_name in scope_functions.items():
        for name, (node, is_async) in by_name.items():
            if _line_count(node) < MIN_TWIN_FUNCTION_LINES:
                continue
            for candidate in _twin_candidates(name):
                if candidate == name or candidate not in by_name:
                    continue
                other_node, other_is_async = by_name[candidate]
                pair_key = frozenset({(name, class_name or ""), (candidate, class_name or "")})
                if pair_key in reported:
                    continue
                ratio = difflib.SequenceMatcher(
                    None, ast.unparse(node), ast.unparse(other_node)
                ).ratio()
                if ratio >= min_ratio:
                    reported.add(pair_key)
                    issues.append(
                        Issue(
                            file=filepath,
                            line=min(node.lineno, other_node.lineno),
                            severity="warning",
                            rule="sync-async-duplication",
                            package=pkg_name,
                            message=(
                                f"{name}() and {candidate}() are {ratio:.0%} similar — "
                                "likely copy-pasted sync/async twins; extract a shared helper "
                                "for the non-blocking parts"
                            ),
                        )
                    )
    return issues


# ── Rule: Orphan Interfaces ───────────────────────────────────────────────────


def check_orphan_interfaces_pkg(pkg_name: str, files: list[tuple[Path, ast.Module]]) -> list[Issue]:
    """Detect abstract classes with exactly one concrete implementation.

    This is a classic 'Speculative Generality' code smell. If an interface
    only exists to be implemented by a single class, it is likely adding
    mental overhead without providing any polymorphic value.
    """
    interfaces: dict[str, tuple[Path, ast.ClassDef]] = {}
    implementations: dict[str, list[str]] = defaultdict(list)

    for filepath, tree in files:
        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef):
                continue

            is_abstract = False

            for base in node.bases:
                base_name = None
                if isinstance(base, ast.Name):
                    base_name = base.id
                elif isinstance(base, ast.Attribute):
                    base_name = base.attr

                if base_name in ("ABC", "Protocol"):
                    is_abstract = True
                elif base_name:
                    implementations[base_name].append(node.name)

            for keyword in node.keywords:
                if keyword.arg == "metaclass":
                    val_name = getattr(keyword.value, "id", getattr(keyword.value, "attr", ""))
                    if val_name == "ABCMeta":
                        is_abstract = True

            if is_abstract:
                interfaces[node.name] = (filepath, node)

    issues: list[Issue] = []

    for iface_name, (filepath, node) in interfaces.items():
        impls = implementations.get(iface_name, [])

        if len(impls) == 1:
            impl_name = impls[0]
            issues.append(
                Issue(
                    file=filepath,
                    line=node.lineno,
                    severity="info",
                    rule="orphan-interface",
                    package=pkg_name,
                    message=(
                        f"Interface '{iface_name}' has exactly one implementation "
                        f"('{impl_name}'). Consider removing the abstraction."
                    ),
                )
            )

    return issues
