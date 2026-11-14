"""Cross-file, whole-package checks (duplicate functions, sync/async twins, orphan
interfaces) plus the import-resolution helpers the workspace-level graph reuses."""

from __future__ import annotations

import ast
import difflib
import hashlib
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

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
    "ClassFact",
    "FileFacts",
    "FunctionFact",
    "TwinFact",
    "check_duplicate_functions_pkg",
    "check_orphan_interfaces_pkg",
    "check_sync_async_twins_pkg",
    "collect_facts",
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


# ── Per-file facts ───────────────────────────────────────────────────────────
#
# The package-level checks need a little about every function and class in the
# package, never the trees themselves. A worker that has just parsed a file
# records those facts while the tree is in hand; the parent combines facts
# across files. Facts carry raw measurements (line counts, similarity ratios)
# and the checks apply the thresholds, so one set of facts serves any
# ``--min-duplicate-lines`` or ``--twin-similarity`` and can live in the file
# cache.


@dataclass(frozen=True)
class FunctionFact:
    """A function worth comparing for duplication: not a stub, not a dunder."""

    name: str
    line: int
    column: int
    end_line: int | None
    end_column: int | None
    lines: int
    body: str
    """SHA-1 of the dumped body statements; equal bodies hash equal."""


@dataclass(frozen=True)
class ClassFact:
    name: str
    line: int
    column: int
    end_line: int | None
    end_column: int | None
    bases: tuple[str, ...]
    abstract: bool


@dataclass(frozen=True)
class TwinFact:
    """``name`` has a plausible sync/async counterpart in the same scope."""

    name: str
    counterpart: str
    scope: str
    """Enclosing class name, or ``""`` at module level."""
    line: int
    lines: int
    """Line count of ``name``'s definition (the threshold applies to it alone)."""
    ratio: float


@dataclass
class FileFacts:
    functions: list[FunctionFact] = field(default_factory=list)
    classes: list[ClassFact] = field(default_factory=list)
    twins: list[TwinFact] = field(default_factory=list)

    def to_json(self) -> dict[str, Any]:
        return {
            "functions": [
                [f.name, f.line, f.column, f.end_line, f.end_column, f.lines, f.body]
                for f in self.functions
            ],
            "classes": [
                [c.name, c.line, c.column, c.end_line, c.end_column, list(c.bases), c.abstract]
                for c in self.classes
            ],
            "twins": [
                [t.name, t.counterpart, t.scope, t.line, t.lines, t.ratio] for t in self.twins
            ],
        }

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> FileFacts:
        return cls(
            functions=[FunctionFact(*f) for f in data["functions"]],
            classes=[
                ClassFact(c[0], c[1], c[2], c[3], c[4], tuple(c[5]), c[6]) for c in data["classes"]
            ],
            twins=[TwinFact(*t) for t in data["twins"]],
        )


def collect_facts(tree: ast.Module) -> FileFacts:
    facts = FileFacts()
    for node in _walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if _is_trivial_body(node) or _DUNDER_RE.match(node.name):
                continue  # ``__repr__``/``__eq__`` across sibling classes are protocol hooks
            body = hashlib.sha1(_dump_stmts(node.body).encode("utf-8")).hexdigest()
            facts.functions.append(
                FunctionFact(
                    node.name, node.lineno, **_loc(node), lines=_line_count(node), body=body
                )
            )
        elif isinstance(node, ast.ClassDef):
            bases = _base_names_of(node)
            facts.classes.append(
                ClassFact(
                    node.name,
                    node.lineno,
                    **_loc(node),
                    bases=tuple(bases),
                    abstract=bool(_ABSTRACT_BASES & set(bases)) or _has_abc_metaclass(node),
                )
            )
    facts.twins = _twin_facts(tree)
    return facts


# ── Duplicate Function Bodies (whole-package) ─────────────────────────────────

# A body needs at least this many occurrences to be a "duplicate" at all.
_MIN_DUPLICATE_OCCURRENCES = 2


def check_duplicate_functions_pkg(
    pkg_name: str, files: list[tuple[Path, FileFacts]], min_lines: int
) -> list[Issue]:
    """Finds functions/methods with byte-for-byte identical bodies anywhere in
    the package."""
    groups: dict[str, list[tuple[Path, FunctionFact]]] = defaultdict(list)
    for filepath, facts in files:
        for fn in facts.functions:
            if fn.lines >= min_lines:
                groups[fn.body].append((filepath, fn))

    issues: list[Issue] = []
    for occurrences in groups.values():
        if len(occurrences) < _MIN_DUPLICATE_OCCURRENCES:
            continue
        occurrences.sort(key=lambda item: (str(item[0]), item[1].line))
        locations = ", ".join(
            f"{_display_path(fp)}:{fn.line} ({fn.name})" for fp, fn in occurrences
        )
        first_fp, first = occurrences[0]
        issues.append(
            Issue(
                file=first_fp,
                line=first.line,
                column=first.column,
                end_line=first.end_line,
                end_column=first.end_column,
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


def _twin_facts(tree: ast.Module) -> list[TwinFact]:
    """Every (function, plausible counterpart) pair in one scope of the file,
    with how similar their source text is. Scopes are visited in tree order,
    pairs in definition order, counterparts in sorted order, so the list is
    the same on every run."""
    scopes: dict[str | None, dict[str, ast.FunctionDef | ast.AsyncFunctionDef]] = defaultdict(dict)
    for node, class_name in _walk_with_class_context(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            scopes[class_name][node.name] = node
    facts: list[TwinFact] = []
    for class_name, by_name in scopes.items():
        facts.extend(_twins_in_scope(class_name or "", by_name))
    return facts


def _twins_in_scope(
    scope: str, by_name: dict[str, ast.FunctionDef | ast.AsyncFunctionDef]
) -> list[TwinFact]:
    text: dict[str, str] = {}

    def unparsed(name: str) -> str:
        if name not in text:
            text[name] = ast.unparse(by_name[name])
        return text[name]

    facts: list[TwinFact] = []
    for name, node in by_name.items():
        # Sorted: a set's iteration order follows the hash seed, and a function
        # with two counterparts (get / aget / get_async) must report them in
        # the same order on every run.
        for counterpart in sorted(_twin_candidates(name)):
            if counterpart == name or counterpart not in by_name:
                continue
            other = by_name[counterpart]
            ratio = difflib.SequenceMatcher(None, unparsed(name), unparsed(counterpart)).ratio()
            line = min(node.lineno, other.lineno)
            facts.append(TwinFact(name, counterpart, scope, line, _line_count(node), ratio))
    return facts


def check_sync_async_twins_pkg(
    pkg_name: str, files: list[tuple[Path, FileFacts]], min_ratio: float
) -> list[Issue]:
    """Flags function pairs that look like a sync/async "twin" (by name) and
    whose bodies are highly similar text."""
    issues: list[Issue] = []
    for filepath, facts in files:
        # A pair is one finding however it is reached (read → aread and
        # aread → read). The scope is part of the key: Request.read/aread and
        # Response.read/aread in one file are two twin pairs, not one.
        reported: set[tuple[str, frozenset[str]]] = set()
        for twin in facts.twins:
            if twin.lines < _cfg.MIN_TWIN_FUNCTION_LINES:
                continue
            pair_key = (twin.scope, frozenset({twin.name, twin.counterpart}))
            if pair_key in reported or twin.ratio < min_ratio:
                continue
            reported.add(pair_key)
            issues.append(
                Issue(
                    file=filepath,
                    line=twin.line,
                    severity="warning",
                    rule="sync-async-duplication",
                    package=pkg_name,
                    message=(
                        f"{twin.name}() and {twin.counterpart}() are {twin.ratio:.0%} similar — "
                        "likely copy-pasted sync/async twins; extract a shared helper "
                        "for the non-blocking parts"
                    ),
                )
            )
    return issues


# ── Rule: Orphan Interfaces ───────────────────────────────────────────────────


def check_orphan_interfaces_pkg(pkg_name: str, files: list[tuple[Path, FileFacts]]) -> list[Issue]:
    """Detect abstract classes with exactly one concrete implementation.

    This is a classic 'Speculative Generality' code smell. If an interface
    only exists to be implemented by a single class, it is likely adding
    mental overhead without providing any polymorphic value.
    """
    interfaces: dict[str, tuple[Path, ClassFact]] = {}
    implementations: dict[str, list[str]] = defaultdict(list)
    for filepath, facts in files:
        for cls in facts.classes:
            for base_name in cls.bases:
                if base_name not in _ABSTRACT_BASES:
                    implementations[base_name].append(cls.name)
            if cls.abstract:
                interfaces[cls.name] = (filepath, cls)

    issues: list[Issue] = []
    for iface_name, (filepath, cls) in interfaces.items():
        impls = implementations.get(iface_name, [])
        if len(impls) != 1:
            continue
        issues.append(
            Issue(
                file=filepath,
                line=cls.line,
                column=cls.column,
                end_line=cls.end_line,
                end_column=cls.end_column,
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
