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
import re
import sys
from collections import Counter
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
    py_file: Path, pkg_name: str, result: ScanResult, cache: FileCache | None = None
) -> tuple[str, ast.Module | None] | None:
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
        return None
    result.total_lines += _file_line_count(source)

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
        return source, None

    entry = cache.load(source, file=py_file, package=pkg_name) if cache is not None else None
    if entry is not None:
        result.cache_hits += 1
        _apply_cached(py_file, result, entry)
        return source, _parse_quietly(source, py_file)
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
        result.issues.append(
            _infra_issue(
                py_file,
                pkg_name,
                "scan-limit",
                "File skipped — nesting deeper than the analysis limit; exclude it if generated",
            )
        )
        return source, None
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
            ),
        )
    return source, tree


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


def _parse_quietly(source: str, py_file: Path) -> ast.Module | None:
    """The tree for the package-level checks on a cache hit; the syntax-error
    finding itself came from the cache."""
    try:
        return ast.parse(source, filename=str(py_file))
    except (SyntaxError, RecursionError, MemoryError):
        return None


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


def _apply_suppressions(result: ScanResult, source_lines_by_file: dict[Path, list[str]]) -> None:
    kept: list[Issue] = []
    for issue in result.issues:
        lines = source_lines_by_file.get(issue.file)
        sup = _is_suppressed(issue, lines) if lines is not None else None
        if sup is not None:
            result.suppressed += 1
            result.ignored.append(dataclasses.replace(issue, reason=sup.reason))
        else:
            kept.append(issue)
    result.issues = kept


def scan_package(pkg_name: str, pkg_path: Path, *, config: ScanConfig) -> ScanResult:
    result = ScanResult()
    if not pkg_path.exists():
        return result

    parsed_files: list[tuple[Path, ast.Module]] = []
    source_lines_by_file: dict[Path, list[str]] = {}
    cache = FileCache(config.cache_dir, config.cache_key) if config.cache_dir else None

    for py_file in _iter_package_files(pkg_path, config):
        scanned = _scan_one_file(py_file, pkg_name, result, cache)
        if scanned is None:
            continue
        source, tree = scanned
        source_lines_by_file[py_file] = source.splitlines()
        if tree is not None:
            parsed_files.append((py_file, tree))

    for pkg_check_fn in PACKAGE_CHECKS:
        result.issues.extend(pkg_check_fn(pkg_name, parsed_files))
    result.issues.extend(
        check_duplicate_functions_pkg(pkg_name, parsed_files, config.min_duplicate_lines)
    )
    result.issues.extend(check_sync_async_twins_pkg(pkg_name, parsed_files, config.twin_similarity))

    _finalize(result, pkg_path, source_lines_by_file)
    return result


def _finalize(
    result: ScanResult, pkg_path: Path, source_lines_by_file: dict[Path, list[str]]
) -> None:
    """Rule overrides, character columns, fingerprints, inline suppressions —
    in that order, for every issue in ``result``."""
    result.issues = _apply_rule_overrides(result.issues)
    for issue in result.issues:
        _normalize_columns(issue, source_lines_by_file.get(issue.file))
    _assign_fingerprints(result.issues, pkg_path)
    _apply_suppressions(result, source_lines_by_file)


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
    lines_cache: dict[Path, list[str]] = {}

    def lines_of(path: Path) -> list[str]:
        if path not in lines_cache:
            try:
                lines_cache[path] = path.read_text(encoding="utf-8", errors="replace").splitlines()
            except OSError:
                lines_cache[path] = []
        return lines_cache[path]

    for pkg_name, result in per_package.items():
        own = [i for i in found if i.package == pkg_name]
        result.raw_imports = {}
        result.token_streams = {}
        if not own:
            continue
        staging = ScanResult(issues=own)
        _finalize(staging, PACKAGES[pkg_name], {i.file: lines_of(i.file) for i in own})
        result.issues.extend(staging.issues)
        result.suppressed += staging.suppressed
        result.ignored.extend(staging.ignored)


def _init_worker(worker_packages: dict[str, Path], overrides: dict[str, Any] | None = None) -> None:
    """Initializer for process pool workers: replicate the parent's package
    registry and applied settings into the fresh interpreter."""
    global PACKAGES  # spaghetti-ignore[scope-mutation]: intentional — worker init must set process-local registry
    PACKAGES = worker_packages
    if overrides:
        from spaghetti.settings import apply_overrides

        apply_overrides(overrides)


async def _review_one(
    pkg_name: str,
    *,
    config: ScanConfig,
    executor: concurrent.futures.Executor,
) -> tuple[str, ScanResult]:
    loop = asyncio.get_running_loop()
    # Look ``scan_package`` up on the module at call time so tests can
    # monkeypatch it.
    func = partial(scan_package, pkg_name, PACKAGES[pkg_name], config=config)
    return pkg_name, await loop.run_in_executor(executor, func)


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
    if executor is None:
        own_executor = concurrent.futures.ProcessPoolExecutor(
            initializer=_init_worker, initargs=(PACKAGES, overrides)
        )
        executor = own_executor
    try:
        pairs = await asyncio.gather(
            *(
                _review_one(
                    pkg_name,
                    config=dataclasses.replace(config, recursive=pkg_name not in non_recursive),
                    executor=executor,
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
