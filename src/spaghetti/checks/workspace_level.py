"""Checks that need every registered package at once: import cycles and
coupling over the union import graph, and token-based code clones.

Workers (``detector.scan_package``) collect the lightweight inputs — raw
import statements and normalised token streams per file — and the parent
process runs these checks once over all packages, so a cycle or clone that
spans two packages is found and a single-package run reports exactly what the
old per-package checks did.
"""

from __future__ import annotations

import ast
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

import spaghetti.config as _cfg
from spaghetti.checks.package_level import (
    _import_targets,
    _module_and_package_for,
    _module_level_imports,
    _package_prefix,
)
from spaghetti.clones import TokenStream, duplicated_regions, find_clone_groups
from spaghetti.contracts import Contract, violation_reason
from spaghetti.models import Issue, _display_path

__all__ = [
    "RawImport",
    "collect_raw_imports",
    "build_import_graph",
    "dependency_edges",
    "check_contracts_ws",
    "check_import_cycles_ws",
    "check_module_coupling_ws",
    "check_duplicate_code_ws",
]


@dataclass(frozen=True)
class RawImport:
    """One module-level import statement, unresolved. ``level`` is -1 for a
    plain ``import a.b`` (``module`` then holds the dotted name and ``names``
    is empty); otherwise it is the ``from`` statement's relative level."""

    level: int
    module: str | None
    names: tuple[str, ...]
    line: int = 1


def collect_raw_imports(tree: ast.Module) -> list[RawImport]:
    raw: list[RawImport] = []
    for node in _module_level_imports(tree):
        if isinstance(node, ast.ImportFrom):
            raw.append(
                RawImport(node.level, node.module, tuple(a.name for a in node.names), node.lineno)
            )
        else:
            for alias in node.names:
                raw.append(RawImport(-1, alias.name, (), node.lineno))
    return raw


Graph = dict[str, set[str]]
ModuleIndex = dict[str, tuple[str, Path]]
"""module -> (owning package name, file)."""


def _module_index(imports_by_package: dict[str, dict[Path, list[RawImport]]]) -> ModuleIndex:
    index: ModuleIndex = {}
    for pkg_name, files in imports_by_package.items():
        if not _package_prefix(pkg_name):
            continue
        for filepath in files:
            module, _ = _module_and_package_for(pkg_name, filepath)
            index[module] = (pkg_name, filepath)
    return index


def _resolve(raw: RawImport, package: str, modules: dict[str, Path]) -> list[str]:
    """Dotted module names one raw import statement depends on."""
    if raw.level == -1:
        return [raw.module] if raw.module else []
    node = ast.ImportFrom(
        module=raw.module, names=[ast.alias(name=n) for n in raw.names], level=raw.level
    )
    return _import_targets(package, node, modules)


def build_import_graph(
    imports_by_package: dict[str, dict[Path, list[RawImport]]],
) -> tuple[Graph, ModuleIndex]:
    """Union import graph over every package: module -> modules it imports,
    keeping only edges into a registered package. ``from pkg import name``
    resolves to the submodule ``pkg.name`` when that module exists anywhere in
    the workspace."""
    index = _module_index(imports_by_package)
    modules = {module: entry[1] for module, entry in index.items()}
    stems = [_package_prefix(p).rstrip(".") for p in imports_by_package if _package_prefix(p)]

    def in_workspace(name: str) -> bool:
        return any(name == stem or name.startswith(stem + ".") for stem in stems)

    graph: Graph = defaultdict(set)
    for module, (pkg_name, filepath) in index.items():
        _, package = _module_and_package_for(pkg_name, filepath)
        for raw in imports_by_package[pkg_name][filepath]:
            graph[module].update(
                t for t in _resolve(raw, package, modules) if t and in_workspace(t)
            )
    return graph, index


Edge = tuple[str, str, int]
"""(importing module, imported dotted name, line) — every import, third-party included."""


def dependency_edges(
    imports_by_package: dict[str, dict[Path, list[RawImport]]],
) -> tuple[list[Edge], ModuleIndex]:
    """Every resolved import edge in file order, workspace and external alike,
    for the architecture-contract check."""
    index = _module_index(imports_by_package)
    modules = {module: entry[1] for module, entry in index.items()}
    edges: list[Edge] = []
    for module, (pkg_name, filepath) in index.items():
        _, package = _module_and_package_for(pkg_name, filepath)
        for raw in imports_by_package[pkg_name][filepath]:
            edges.extend((module, t, raw.line) for t in _resolve(raw, package, modules) if t)
    return edges, index


def check_contracts_ws(
    edges: list[Edge], index: ModuleIndex, contracts: list[Contract]
) -> list[Issue]:
    """One finding per (import, contract) pair that breaks a contract."""
    issues: list[Issue] = []
    for module, target, line in edges:
        for contract in contracts:
            reason = violation_reason(contract, module, target)
            if reason is None:
                continue
            pkg_name, filepath = index[module]
            issues.append(
                Issue(
                    file=filepath,
                    line=line,
                    severity="error",
                    rule="contract-violation",
                    package=pkg_name,
                    message=(
                        f"Contract '{contract.name}': {module} imports {target} — {reason}; "
                        "depend on an abstraction (e.g. a typing.Protocol) the lower layer "
                        "implements and inject it instead (Dependency Inversion Principle)"
                    ),
                )
            )
    return issues


_CYCLE_HINT = (
    " — extract a shared abstraction (e.g. a typing.Protocol) both sides can depend on, "
    "and inject it instead of importing directly to break the cycle "
    "(Dependency Inversion Principle / Dependency Injection)"
)


def check_import_cycles_ws(graph: Graph, index: ModuleIndex) -> list[Issue]:
    """Every distinct import cycle, found by DFS; attributed to the package of
    the module where the cycle was entered."""
    if not index:
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
        for neighbor in sorted(graph.get(current, ())):  # sorted: deterministic representative
            if neighbor in on_stack:
                cycle_start = stack.index(neighbor)
                cycle = [*stack[cycle_start:], neighbor]
                key = frozenset(cycle)
                entry = index.get(stack[cycle_start])
                if key not in reported and entry is not None:
                    reported.add(key)
                    pkg_name, loc = entry
                    issues.append(
                        Issue(
                            file=loc,
                            line=1,
                            severity="error",
                            rule="import-cycle",
                            package=pkg_name,
                            message="Circular import: " + " → ".join(cycle) + _CYCLE_HINT,
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


def check_module_coupling_ws(graph: Graph, index: ModuleIndex) -> list[Issue]:
    """A module with *both* fan-in and fan-out over their thresholds is a hub
    whose changes ripple; either alone is usually a legitimate util module or
    a thin orchestrator."""
    fan_in: dict[str, int] = defaultdict(int)
    for imports in graph.values():
        for target in imports:
            fan_in[target] += 1
    issues: list[Issue] = []
    for module, (pkg_name, filepath) in index.items():
        module_fan_out = len(graph.get(module, ()))
        module_fan_in = fan_in.get(module, 0)
        if module_fan_in > _cfg.MAX_MODULE_FAN_IN and module_fan_out > _cfg.MAX_MODULE_FAN_OUT:
            issues.append(
                Issue(
                    file=filepath,
                    line=1,
                    severity="warning",
                    rule="high-coupling",
                    package=pkg_name,
                    message=(
                        f"module {module} has fan-in={module_fan_in} and "
                        f"fan-out={module_fan_out} (max {_cfg.MAX_MODULE_FAN_IN}/{_cfg.MAX_MODULE_FAN_OUT}) "
                        "— other modules depend on it heavily and it depends on other "
                        "modules heavily; consider splitting it or inverting some "
                        "dependencies (Dependency Inversion Principle)"
                    ),
                )
            )
    return issues


_MAX_LISTED_SITES = 5


def check_duplicate_code_ws(
    streams_by_package: dict[str, dict[Path, TokenStream]], min_tokens: int
) -> list[Issue]:
    """One finding per duplicated region of a file (overlapping clone sites
    merged), listing where else the code appears. Every side of a clone gets a
    finding, as in SonarQube's duplicated-blocks view, so the owner of each
    copy sees it. ``info`` by default: duplication is a density signal (Sonar
    scores it as a metric, not a defect), and mature libraries carry
    deliberate copies — sync/async twins, router/app mirrors — that a
    per-block warning would turn into grade-sinking noise."""
    owner: dict[Path, str] = {}
    streams: dict[Path, TokenStream] = {}
    for pkg_name, files in streams_by_package.items():
        for path, stream in files.items():
            owner[path] = pkg_name
            streams[path] = stream
    groups = find_clone_groups(streams, min_tokens)
    issues: list[Issue] = []
    for region in duplicated_regions(groups, list(streams)):
        listed = region.others[:_MAX_LISTED_SITES]
        where = ", ".join(f"{_display_path(s.file)}:{s.start_line}-{s.end_line}" for s in listed)
        if len(region.others) > _MAX_LISTED_SITES:
            where += f" and {len(region.others) - _MAX_LISTED_SITES} more"
        issues.append(
            Issue(
                file=region.file,
                line=region.start_line,
                end_line=region.end_line,
                severity="info",
                rule="duplicate-code",
                package=owner[region.file],
                message=(
                    f"Lines {region.start_line}-{region.end_line} are duplicated in "
                    f"{len(region.others)} other place(s): {where} — extract the shared "
                    "logic into one function; renamed variables do not make a copy independent"
                ),
            )
        )
    return issues
