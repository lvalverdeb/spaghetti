#!/usr/bin/env python3
"""Spaghetti Code Detection Script.

Owns the concurrent scan orchestration (``scan_package``,
``review_packages_concurrently``) and the mutable ``PACKAGES`` registry — the
single place other modules (``cli.py``, ``checks/*.py``) late-import
(``import spaghetti.detector as _det``) to read or monkeypatch the active
package registry without a circular import at module load time.

Everything else lives in its own module:
  - spaghetti.config          (workspace root, thresholds, layer rules)
  - spaghetti.models          (Issue, ScanResult, RemediationStep)
  - spaghetti.suppression     (inline spaghetti-ignore markers)
  - spaghetti.ast_helpers     (AST utility functions)
  - spaghetti.checks.*        (check functions)
  - spaghetti.scoring         (health score, remediation plan)
  - spaghetti.cli             (CLI entry point, resolve_packages)
"""

from __future__ import annotations

import ast
import asyncio
import concurrent.futures
import dataclasses
import hashlib
import os
import re
import sys
from collections import Counter
from collections.abc import Callable
from functools import partial
from pathlib import Path
from typing import Any

import spaghetti.config as _cfg
from spaghetti.ast_helpers import _file_line_count
from spaghetti.cache import FileCache, FileEntry
from spaghetti.checks import ALL_CHECKS, PACKAGE_CHECKS, SOURCE_AWARE_CHECKS, SOURCE_CHECKS
from spaghetti.checks.ast_per_file import low_maintainability_issue
from spaghetti.checks.package_level import (
    check_duplicate_functions_pkg,
    check_sync_async_twins_pkg,
    collect_facts,
)
from spaghetti.checks.workspace_level import (
    build_import_graph,
    check_contracts_ws,
    check_duplicate_code_ws,
    check_import_cycles_ws,
    check_module_coupling_ws,
    collect_raw_imports,
    dependency_edges,
)
from spaghetti.clones import token_stream
from spaghetti.metrics import maintainability_index
from spaghetti.models import Issue, ScanConfig, ScanResult
from spaghetti.rules import DEFAULT_RULE_IDS, RULE_CATALOG
from spaghetti.suppression import _is_suppressed

# Empty until cli.main() sets it (either via discover_cwd_packages or
# resolve_packages) -- there's no hardcoded workspace-specific default to
# fall back to, since a generic pip-install-anywhere tool shouldn't assume
# it's running inside this particular monorepo.
PACKAGES: dict[str, Path] = {}


# ── Scanner ───────────────────────────────────────────────────────────────────


def _iter_package_files(pkg_path: Path, config: ScanConfig) -> list[Path]:
    glob_fn = pkg_path.rglob if config.recursive else pkg_path.glob
    files: list[Path] = []
    for py_file in sorted(glob_fn("*.py")):
        path_str = str(py_file)
        if "__pycache__" in path_str:
            continue
        if any(pattern in path_str for pattern in config.exclude):
            continue
        files.append(py_file)
    return files


def _infra_issue(py_file: Path, pkg_name: str, rule: str, message: str) -> Issue:
    return Issue(
        file=py_file,
        line=1,
        severity=RULE_CATALOG[rule].default_severity,
        rule=rule,
        package=pkg_name,
        message=message,
    )


def _scan_one_file(
    py_file: Path,
    pkg_name: str,
    pkg_path: Path,
    result: ScanResult,
    cache: FileCache | None = None,
) -> None:
    """Run every per-file check on ``py_file``, appending findings to ``result``.

    Returns ``(source, tree)`` — ``tree`` is ``None`` when the file failed to
    parse or was skipped for exceeding a limit — or ``None`` when the file
    could not be read at all.
    """
    result.files_scanned += 1
    try:
        source = py_file.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        result.issues.append(
            _infra_issue(
                py_file, pkg_name, "unreadable-file", f"Failed to read file — {exc.strerror or exc}"
            )
        )
        _finalize_new(result, len(result.issues) - 1, pkg_path, None)
        return
    result.total_lines += _file_line_count(source)
    start = len(result.issues)
    _scan_source(source, py_file, pkg_name, result, cache)
    _finalize_new(result, start, pkg_path, source.splitlines())


def _scan_source(
    source: str, py_file: Path, pkg_name: str, result: ScanResult, cache: FileCache | None
) -> None:
    """Size limit, cache lookup, then the per-file checks (or the cached result)."""

    size = len(source.encode("utf-8", errors="replace"))
    if size > _cfg.MAX_FILE_BYTES:
        result.issues.append(
            _infra_issue(
                py_file,
                pkg_name,
                "scan-limit",
                f"File skipped — {size} bytes exceeds max-file-bytes ({_cfg.MAX_FILE_BYTES}); "
                "exclude it if generated, or raise the limit",
            )
        )
        return

    entry = cache.load(source, file=py_file, package=pkg_name) if cache is not None else None
    if entry is not None:
        result.cache_hits += 1
        _apply_cached(py_file, result, entry)
        return
    if cache is not None:
        result.cache_misses += 1

    start = len(result.issues)
    functions_before = result.functions_scanned
    try:
        tree = _check_one_file(source, py_file, pkg_name, result)
    except (RecursionError, MemoryError):
        del result.issues[start:]
        result.functions_scanned = functions_before
        result.file_mi.pop(py_file, None)
        result.raw_imports.pop(py_file, None)
        result.token_streams.pop(py_file, None)
        result.facts.pop(py_file, None)
        result.issues.append(
            _infra_issue(
                py_file,
                pkg_name,
                "scan-limit",
                "File skipped — nesting deeper than the analysis limit; exclude it if generated",
            )
        )
        return
    if cache is not None:
        cache.store(
            source,
            FileEntry(
                issues=result.issues[start:],
                parsed=tree is not None,
                functions=result.functions_scanned - functions_before,
                mi=result.file_mi.get(py_file),
                raw_imports=result.raw_imports.get(py_file),
                tokens=result.token_streams.get(py_file),
                facts=result.facts.get(py_file),
            ),
            file=py_file,
            package=pkg_name,
        )


def _check_one_file(
    source: str, py_file: Path, pkg_name: str, result: ScanResult
) -> ast.Module | None:
    """Every per-file check, uncached. Returns the tree, or ``None`` on a syntax error."""
    for source_check in SOURCE_CHECKS:
        result.issues.extend(source_check(source, py_file, pkg_name))

    try:
        tree = ast.parse(source, filename=str(py_file))
    except SyntaxError:
        result.issues.append(
            _infra_issue(py_file, pkg_name, "syntax-error", "Failed to parse file — syntax error")
        )
        return None

    result.functions_scanned += sum(
        isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) for node in ast.walk(tree)
    )
    for check_fn in ALL_CHECKS:
        result.issues.extend(check_fn(tree, py_file, pkg_name))
    for source_aware_check in SOURCE_AWARE_CHECKS:
        result.issues.extend(source_aware_check(tree, source, py_file, pkg_name))
    mi = maintainability_index(tree, source)
    result.file_mi[py_file] = mi
    result.issues.extend(low_maintainability_issue(mi, py_file, pkg_name))
    result.raw_imports[py_file] = collect_raw_imports(tree)
    result.token_streams[py_file] = token_stream(tree)
    result.facts[py_file] = collect_facts(tree)
    return tree


def _apply_cached(py_file: Path, result: ScanResult, entry: FileEntry) -> None:
    result.issues.extend(entry.issues)
    result.functions_scanned += entry.functions
    if entry.mi is not None:
        result.file_mi[py_file] = entry.mi
    if entry.raw_imports is not None:
        result.raw_imports[py_file] = entry.raw_imports
    if entry.tokens is not None:
        result.token_streams[py_file] = entry.tokens
    if entry.facts is not None:
        result.facts[py_file] = entry.facts


def _char_column(line_text: str, byte_offset: int) -> int:
    """Convert a 0-based UTF-8 byte offset (what ``ast`` reports) into a
    1-based character column (what editors and SARIF expect)."""
    return len(line_text.encode("utf-8")[:byte_offset].decode("utf-8", errors="replace")) + 1


def _normalize_columns(issue: Issue, lines: list[str] | None) -> None:
    """Checks record raw ``ast`` byte offsets; rewrite them to character columns."""
    if lines is None:
        return
    if issue.column is not None and 0 < issue.line <= len(lines):
        issue.column = _char_column(lines[issue.line - 1], issue.column - 1)
    if (
        issue.end_column is not None
        and issue.end_line is not None
        and 0 < issue.end_line <= len(lines)
    ):
        issue.end_column = _char_column(lines[issue.end_line - 1], issue.end_column - 1)


_DIGITS_RE = re.compile(r"\d+")


def _assign_fingerprints(issues: list[Issue], pkg_path: Path) -> None:
    """Stable identity for a finding across line shifts and re-runs.

    Hashes rule + package-relative path + message with digits stripped (so
    "is 65 lines" and "is 66 lines" stay the same finding). Identical keys
    within one run are disambiguated by order of appearance so a baseline
    can hold two genuinely separate occurrences.
    """
    seen: Counter[str] = Counter()
    for issue in issues:
        try:
            rel = issue.file.relative_to(pkg_path).as_posix()
        except ValueError:
            rel = issue.file.name
        key = f"{issue.rule}\0{rel}\0{_DIGITS_RE.sub('', issue.message)}"
        n = seen[key]
        seen[key] += 1
        issue.fingerprint = hashlib.sha1(f"{key}\0{n}".encode()).hexdigest()[:16]


def _apply_rule_overrides(issues: list[Issue]) -> list[Issue]:
    """Honour the catalog's ``enabled_by_default`` and ``[tool.spaghetti.rules]``:
    drop rules that ship off unless opted in, drop rules set to ``off``, rewrite
    the severity of the rest. Runs before fingerprinting so a severity change
    doesn't alter a finding's identity (the fingerprint ignores severity)."""
    overrides = _cfg.RULE_OVERRIDES
    kept: list[Issue] = []
    for issue in issues:
        setting = overrides.get(issue.rule)
        if setting == "off":
            continue
        if setting is None:
            if issue.rule in RULE_CATALOG and issue.rule not in DEFAULT_RULE_IDS:
                continue  # ships off; the user has to opt in
        else:
            issue.severity = setting  # type: ignore[assignment]
        kept.append(issue)
    return kept


class _SourceLines:
    """Source lines of files read on demand, each at most once, for the
    checks whose findings arrive after the per-file pass has let go of the
    text. An unreadable file yields ``None`` and its findings are left as
    they are, like a file that was never read."""

    def __init__(self) -> None:
        self._cache: dict[Path, list[str] | None] = {}

    def __call__(self, path: Path) -> list[str] | None:
        if path not in self._cache:
            try:
                self._cache[path] = path.read_text(encoding="utf-8", errors="replace").splitlines()
            except OSError:
                self._cache[path] = None
        return self._cache[path]


def _finalize_new(result: ScanResult, start: int, pkg_path: Path, lines: list[str] | None) -> None:
    """Finalize the findings appended since ``start``, all from one file whose
    source lines are ``lines``. Fingerprint occurrence counts are per file and
    rule, so a file's findings can be finalized the moment they exist,
    without waiting for the rest of the package."""
    kept, ignored = _finalize_issues(result.issues[start:], pkg_path, lambda _path: lines)
    result.issues[start:] = kept
    result.suppressed += len(ignored)
    result.ignored.extend(ignored)


def _finalize_issues(
    issues: list[Issue], pkg_path: Path, lines_of: Callable[[Path], list[str] | None]
) -> tuple[list[Issue], list[Issue]]:
    """Rule overrides, character columns, fingerprints, inline suppressions —
    in that order. Returns the findings kept and the ones suppressed."""
    issues = _apply_rule_overrides(issues)
    for issue in issues:
        _normalize_columns(issue, lines_of(issue.file))
    _assign_fingerprints(issues, pkg_path)
    kept: list[Issue] = []
    ignored: list[Issue] = []
    for issue in issues:
        lines = lines_of(issue.file)
        sup = _is_suppressed(issue, lines) if lines is not None else None
        if sup is not None:
            ignored.append(dataclasses.replace(issue, reason=sup.reason))
        else:
            kept.append(issue)
    return kept, ignored


def scan_package(pkg_name: str, pkg_path: Path, *, config: ScanConfig) -> ScanResult:
    """Scan one package in this process: every file, then the package checks."""
    result = ScanResult()
    if not pkg_path.exists():
        return result
    _scan_files_into(result, pkg_name, pkg_path, _iter_package_files(pkg_path, config), config)
    _assemble_package(pkg_name, pkg_path, result, config)
    return result


def _scan_files(pkg_name: str, pkg_path: Path, files: list[Path], config: ScanConfig) -> ScanResult:
    """The per-file half of :func:`scan_package` for one chunk of files, run in
    a worker process. The parent reassembles chunks in file order."""
    result = ScanResult()
    _scan_files_into(result, pkg_name, pkg_path, files, config)
    return result


def _scan_files_into(
    result: ScanResult, pkg_name: str, pkg_path: Path, files: list[Path], config: ScanConfig
) -> None:
    cache = FileCache(config.cache_dir, config.cache_key) if config.cache_dir else None
    for py_file in files:
        _scan_one_file(py_file, pkg_name, pkg_path, result, cache)


def _assemble_package(
    pkg_name: str, pkg_path: Path, result: ScanResult, config: ScanConfig
) -> None:
    """The package-level half: the cross-file checks over the per-file facts,
    finalized with the source of just the files they name. Consumes the facts."""
    facts = list(result.facts.items())
    result.facts = {}
    found: list[Issue] = []
    for pkg_check_fn in PACKAGE_CHECKS:
        found.extend(pkg_check_fn(pkg_name, facts))
    found.extend(check_duplicate_functions_pkg(pkg_name, facts, config.min_duplicate_lines))
    found.extend(check_sync_async_twins_pkg(pkg_name, facts, config.twin_similarity))
    kept, ignored = _finalize_issues(found, pkg_path, _SourceLines())
    result.issues.extend(kept)
    result.suppressed += len(ignored)
    result.ignored.extend(ignored)


def scan_workspace(per_package: dict[str, ScanResult], *, config: ScanConfig) -> None:
    """Run the checks that need every package at once — import cycles and
    coupling over the union graph, token-based clones — and fold their
    findings into each owning package's result. Consumes the per-file
    transport data the workers collected."""
    imports = {name: r.raw_imports for name, r in per_package.items()}
    streams = {name: r.token_streams for name, r in per_package.items()}
    graph, index = build_import_graph(imports)
    found = [
        *check_import_cycles_ws(graph, index),
        *check_module_coupling_ws(graph, index),
        *check_duplicate_code_ws(streams, config.min_clone_tokens),
    ]
    if _cfg.CONTRACTS:
        found.extend(check_contracts_ws(*dependency_edges(imports), _cfg.CONTRACTS))
    lines_of = _SourceLines()
    for pkg_name, result in per_package.items():
        own = [i for i in found if i.package == pkg_name]
        result.raw_imports = {}
        result.token_streams = {}
        if not own:
            continue
        kept, ignored = _finalize_issues(own, PACKAGES[pkg_name], lines_of)
        result.issues.extend(kept)
        result.suppressed += len(ignored)
        result.ignored.extend(ignored)


def _init_worker(worker_packages: dict[str, Path], overrides: dict[str, Any] | None = None) -> None:
    """Initializer for process pool workers: replicate the parent's package
    registry and applied settings into the fresh interpreter."""
    global PACKAGES  # spaghetti-ignore[scope-mutation]: intentional — worker init must set process-local registry
    PACKAGES = worker_packages
    if overrides:
        from spaghetti.settings import apply_overrides

        apply_overrides(overrides)


# Packages with at least this many files are scanned in parallel chunks; smaller
# ones go to one worker whole, which is cheaper than chunk bookkeeping.
PARALLEL_MIN_FILES = 64


def _chunks(files: list[Path], workers: int) -> list[list[Path]]:
    """``files`` split into about ``4 × workers`` contiguous runs so the pool
    stays busy to the end without a long tail from one big chunk."""
    count = min(len(files), max(1, workers) * 4)
    size = -(-len(files) // count)
    return [files[i : i + size] for i in range(0, len(files), size)]


async def _review_one(
    pkg_name: str,
    *,
    config: ScanConfig,
    executor: concurrent.futures.Executor,
    workers: int,
) -> tuple[str, ScanResult]:
    loop = asyncio.get_running_loop()
    pkg_path = PACKAGES[pkg_name]
    files = _iter_package_files(pkg_path, config) if pkg_path.exists() else []
    if len(files) < PARALLEL_MIN_FILES:
        # Look ``scan_package`` up on the module at call time so tests can
        # monkeypatch it.
        func = partial(scan_package, pkg_name, pkg_path, config=config)
        return pkg_name, await loop.run_in_executor(executor, func)
    parts = await asyncio.gather(
        *(
            loop.run_in_executor(executor, partial(_scan_files, pkg_name, pkg_path, chunk, config))
            for chunk in _chunks(files, workers)
        )
    )
    result = ScanResult()
    for part in parts:  # chunk order is file order, so issue order is unchanged
        result.extend(part)
        result.raw_imports.update(part.raw_imports)
        result.token_streams.update(part.token_streams)
        result.facts.update(part.facts)
    _assemble_package(pkg_name, pkg_path, result, config)
    return pkg_name, result


async def review_packages_concurrently(
    pkg_names: list[str],
    *,
    config: ScanConfig,
    executor: concurrent.futures.Executor | None = None,
    non_recursive: frozenset[str] = frozenset(),
    overrides: dict[str, Any] | None = None,
) -> dict[str, ScanResult]:
    """Review every requested package at once, one worker process per package.

    ``non_recursive`` names scan only their own top-level ``*.py`` files —
    used for the synthetic "loose root scripts" package cwd auto-discovery
    can produce alongside real subpackages, so its own directory's already-
    registered subdirectories aren't double-scanned.

    A package whose scan raises propagates the exception to the caller
    rather than being silently dropped from the report.
    """
    own_executor: concurrent.futures.Executor | None = None
    workers = os.cpu_count() or 1
    if executor is None:
        own_executor = concurrent.futures.ProcessPoolExecutor(
            initializer=_init_worker, initargs=(PACKAGES, overrides)
        )
        executor = own_executor
    elif (max_workers := getattr(executor, "_max_workers", None)) is not None:
        workers = max_workers
    try:
        pairs = await asyncio.gather(
            *(
                _review_one(
                    pkg_name,
                    config=dataclasses.replace(config, recursive=pkg_name not in non_recursive),
                    executor=executor,
                    workers=workers,
                )
                for pkg_name in pkg_names
            )
        )
    finally:
        if own_executor is not None:
            own_executor.shutdown(wait=False)

    return dict(pairs)


# ── CLI (must stay here so ``from spaghetti.detector import main`` works) ────


def main() -> int:
    return _cli_main()


def _cli_main() -> int:
    """Actual CLI entry point — delegates to spaghetti.cli.main."""
    from spaghetti.cli import main as _real_main

    return _real_main()


if __name__ == "__main__":
    sys.exit(main())
