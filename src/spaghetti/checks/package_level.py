"""Cross-file, whole-package checks (duplicate functions, sync/async twins, orphan
interfaces) plus the import-resolution helpers the workspace-level graph reuses."""

from __future__ import annotations

import ast
import difflib
from collections import defaultdict
from collections.abc import Iterator
from pathlib import Path

import spaghetti.config as _cfg
from spaghetti.ast_helpers import (
    _dump_stmts,
    _is_trivial_body,
    _line_count,
    _loc,
    _walk_with_class_context,
)
from spaghetti.ast_helpers import (
    walk as _walk,
)
from spaghetti.config import DUNDER_RE as _DUNDER_RE
from spaghetti.models import Issue, _display_path

__all__ = [
    "check_duplicate_functions_pkg",
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


def _import_targets(
    package: str, node: ast.ImportFrom, modules: dict[str, Path] | None = None
) -> list[str]:
    """Resolves an ImportFrom to the dotted module name(s) it depends on.

    ``from pkg import name`` binds the *submodule* ``pkg.name`` when one
    exists, so with ``modules`` (the package's known module names) the edge
    points at that submodule rather than at ``pkg``'s ``__init__`` — the
    ubiquitous ``__init__`` re-export pattern is then not a cycle.
    """
    if node.level == 0:
        base = node.module or ""
    else:
        pkg_parts = package.split(".") if package else []
        up = node.level - 1
        if up:
            pkg_parts = pkg_parts[: len(pkg_parts) - up] if up <= len(pkg_parts) else []
        base = ".".join(pkg_parts)
        if node.module:
            base = f"{base}.{node.module}" if base else node.module
    if not base:
        return [alias.name for alias in node.names]
    if modules is None:
        return [base]
    targets: list[str] = []
    for alias in node.names:
        candidate = f"{base}.{alias.name}"
        targets.append(candidate if candidate in modules else base)
    return sorted(set(targets))


def _package_prefix(pkg_name: str) -> str:
    """Dotted prefix of the package's own modules, e.g. ``"my_lib."``.

    Derived from the registered package directory's name, which is also how
    ``_module_and_package_for`` names modules — so an import is intra-package
    exactly when it starts with this.
    """
    # Late import so tests can monkeypatch detector.PACKAGES
    import spaghetti.detector as _det

    pkg_root = _det.PACKAGES.get(pkg_name)
    return f"{pkg_root.name}." if pkg_root is not None else ""


def _is_type_checking_guard(node: ast.If) -> bool:
    test = node.test
    if isinstance(test, ast.Name) and test.id == "TYPE_CHECKING":
        return True
    return isinstance(test, ast.Attribute) and test.attr == "TYPE_CHECKING"


def _is_main_guard(node: ast.If) -> bool:
    """``if __name__ == "__main__":`` — never executes when the module is imported."""
    test = node.test
    return (
        isinstance(test, ast.Compare)
        and isinstance(test.left, ast.Name)
        and test.left.id == "__name__"
    )


def _module_level_imports(tree: ast.Module) -> list[ast.Import | ast.ImportFrom]:
    """Imports that actually execute at module-import time.

    Excludes imports nested inside a function/method body, ``if TYPE_CHECKING:``
    blocks, and ``if __name__ == "__main__":`` blocks.
    """
    found: list[ast.Import | ast.ImportFrom] = []

    def visit(node: ast.AST) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                continue
            if isinstance(child, ast.If) and (
                _is_type_checking_guard(child) or _is_main_guard(child)
            ):
                continue
            if isinstance(child, (ast.Import, ast.ImportFrom)):
                found.append(child)
            visit(child)

    visit(tree)
    return found


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
        for node in _walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if _line_count(node) < min_lines or _is_trivial_body(node):
                continue
            if _DUNDER_RE.match(node.name):
                continue  # ``__repr__``/``__eq__`` across sibling classes are protocol hooks
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
                **_loc(first_node),
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
        for name, candidate in _twin_pairs(by_name):
            # The class is part of the key: Request.read/aread and Response.read/aread
            # in one file are two twin pairs, not one.
            pair_key = frozenset({(name, class_name or ""), (candidate, class_name or "")})
            if pair_key in reported:
                continue
            node, other_node = by_name[name][0], by_name[candidate][0]
            ratio = difflib.SequenceMatcher(
                None, ast.unparse(node), ast.unparse(other_node)
            ).ratio()
            if ratio < min_ratio:
                continue
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


def _twin_pairs(
    by_name: dict[str, tuple[ast.FunctionDef | ast.AsyncFunctionDef, bool]],
) -> Iterator[tuple[str, str]]:
    """(name, counterpart) for every function in one scope whose plausible twin
    also exists there, skipping bodies too short to compare meaningfully."""
    for name, (node, _is_async) in by_name.items():
        if _line_count(node) < _cfg.MIN_TWIN_FUNCTION_LINES:
            continue
        # Sorted: a set's iteration order follows the hash seed, and a function
        # with two counterparts (get / aget / get_async) must report them in
        # the same order on every run.
        for candidate in sorted(_twin_candidates(name)):
            if candidate != name and candidate in by_name:
                yield name, candidate


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
        for node in _walk(tree):
            if not isinstance(node, ast.ClassDef):
                continue
            base_names = _base_names_of(node)
            for base_name in base_names:
                if base_name not in _ABSTRACT_BASES:
                    implementations[base_name].append(node.name)
            if _ABSTRACT_BASES & set(base_names) or _has_abc_metaclass(node):
                interfaces[node.name] = (filepath, node)

    issues: list[Issue] = []
    for iface_name, (filepath, node) in interfaces.items():
        impls = implementations.get(iface_name, [])
        if len(impls) != 1:
            continue
        issues.append(
            Issue(
                file=filepath,
                line=node.lineno,
                **_loc(node),
                severity="info",
                rule="orphan-interface",
                package=pkg_name,
                message=(
                    f"Interface '{iface_name}' has exactly one implementation "
                    f"('{impls[0]}'). Consider removing the abstraction."
                ),
            )
        )
    return issues


_ABSTRACT_BASES = frozenset({"ABC", "Protocol"})


def _base_names_of(node: ast.ClassDef) -> list[str]:
    names: list[str] = []
    for base in node.bases:
        if isinstance(base, ast.Name):
            names.append(base.id)
        elif isinstance(base, ast.Attribute):
            names.append(base.attr)
    return names


def _has_abc_metaclass(node: ast.ClassDef) -> bool:
    for keyword in node.keywords:
        if keyword.arg == "metaclass":
            value = getattr(keyword.value, "id", getattr(keyword.value, "attr", ""))
            if value == "ABCMeta":
                return True
    return False
