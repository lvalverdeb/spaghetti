#!/usr/bin/env python3
"""Spaghetti Code Detection Script.

Owns the concurrent scan orchestration (``scan_package``,
``SpaghettiReviewAgent``, ``review_packages_concurrently``) and the mutable
``PACKAGES``/``ALLOWED_IMPORT_PREFIXES`` registries — the single place other
modules (``cli.py``, ``checks/*.py``) late-import (``import spaghetti.detector
as _det``) to read or monkeypatch the active package registry without a
circular import at module load time.

Everything else lives in its own module:
  - spaghetti.config          (workspace root, thresholds, layer rules)
  - spaghetti.models          (Issue, ScanResult, RemediationStep)
  - spaghetti.suppression     (inline spaghetti-ignore markers)
  - spaghetti.ast_helpers     (AST utility functions)
  - spaghetti.checks.*        (37 check functions)
  - spaghetti.scoring         (health score, remediation plan)
  - spaghetti.cli             (CLI entry point, resolve_packages)
"""

from __future__ import annotations

import ast
import asyncio
import concurrent.futures
import dataclasses
import sys
from functools import partial
from pathlib import Path
from typing import Any

from boti.core import Agent

from spaghetti.ast_helpers import _file_line_count
from spaghetti.checks import ALL_CHECKS, PACKAGE_CHECKS, SOURCE_CHECKS
from spaghetti.checks.package_level import (
    check_duplicate_functions_pkg,
    check_sync_async_twins_pkg,
)
from spaghetti.config import DEFAULT_PACKAGES as _DEFAULT_PACKAGES
from spaghetti.models import Issue, ScanConfig, ScanResult
from spaghetti.suppression import _is_suppressed

DEFAULT_PACKAGES = dict(_DEFAULT_PACKAGES)

PACKAGES: dict[str, Path] = dict(DEFAULT_PACKAGES)

ALLOWED_IMPORT_PREFIXES: dict[str, list[str]] = {
    "etl-core": ["etl_core."],
    "etl-demo": ["etl_demo."],
    "boti-data": ["boti_data."],
    "boti-dask": ["boti_dask."],
    "boti": ["boti."],
}


# ── Scanner (must stay here for monkeypatch compatibility) ────────────────────


def scan_package(pkg_name: str, pkg_path: Path, *, config: ScanConfig) -> ScanResult:
    result = ScanResult()
    if not pkg_path.exists():
        return result

    parsed_files: list[tuple[Path, ast.Module]] = []
    source_lines_by_file: dict[Path, list[str]] = {}

    glob_fn = pkg_path.rglob if config.recursive else pkg_path.glob
    for py_file in sorted(glob_fn("*.py")):
        path_str = str(py_file)
        if "__pycache__" in path_str:
            continue
        if any(pattern in path_str for pattern in config.exclude):
            continue
        source = py_file.read_text(encoding="utf-8", errors="replace")
        source_lines_by_file[py_file] = source.splitlines()
        result.files_scanned += 1
        result.total_lines += _file_line_count(source)

        for source_check in SOURCE_CHECKS:
            result.issues.extend(source_check(source, py_file, pkg_name))

        try:
            tree = ast.parse(source, filename=str(py_file))
        except SyntaxError:
            result.issues.append(
                Issue(
                    file=py_file,
                    line=1,
                    severity="error",
                    rule="syntax-error",
                    package=pkg_name,
                    message="Failed to parse file — syntax error",
                )
            )
            continue

        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                result.functions_scanned += 1

        for check_fn in ALL_CHECKS:
            result.issues.extend(check_fn(tree, py_file, pkg_name))

        parsed_files.append((py_file, tree))

    for pkg_check_fn in PACKAGE_CHECKS:
        result.issues.extend(pkg_check_fn(pkg_name, parsed_files))
    result.issues.extend(
        check_duplicate_functions_pkg(pkg_name, parsed_files, config.min_duplicate_lines)
    )
    result.issues.extend(check_sync_async_twins_pkg(pkg_name, parsed_files, config.twin_similarity))

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

    return result


def _init_worker(worker_packages: dict[str, Path]) -> None:
    """Initializer for process pool workers."""
    global PACKAGES  # spaghetti-ignore[scope-mutation]: intentional — worker init must set process-local registry
    PACKAGES = worker_packages


class SpaghettiReviewAgent(Agent):
    """Reviews one workspace package for spaghetti-code issues."""

    def __init__(
        self,
        pkg_name: str,
        pkg_path: Path,
        *,
        config: ScanConfig,
        executor: concurrent.futures.ProcessPoolExecutor,
        **agent_kwargs: Any,
    ) -> None:
        super().__init__(**agent_kwargs)
        self.pkg_name = pkg_name
        self.pkg_path = pkg_path
        self.config = config
        self.executor = executor
        self.result: ScanResult | None = None

    async def review(self) -> ScanResult:
        """Run the package scan in a worker process and record the result."""
        self._assert_open()
        loop = asyncio.get_running_loop()

        func = partial(scan_package, self.pkg_name, self.pkg_path, config=self.config)

        result = await loop.run_in_executor(self.executor, func)
        self.result = result
        return result


async def _review_one(agent: SpaghettiReviewAgent) -> tuple[str, ScanResult]:
    async with agent:
        result = await agent.review()
    return agent.pkg_name, result


async def review_packages_concurrently(
    pkg_names: list[str],
    *,
    config: ScanConfig,
    executor: concurrent.futures.Executor | None = None,
    non_recursive: frozenset[str] = frozenset(),
) -> dict[str, ScanResult]:
    """Review every requested package at once using concurrent execution.

    ``non_recursive`` names scan only their own top-level ``*.py`` files —
    used for the synthetic "loose root scripts" package cwd auto-discovery
    can produce alongside real subpackages, so its own directory's already-
    registered subdirectories aren't double-scanned.
    """
    own_executor: concurrent.futures.Executor | None = None
    if executor is None:
        own_executor = concurrent.futures.ProcessPoolExecutor(
            initializer=_init_worker, initargs=(PACKAGES,)
        )
        executor = own_executor
    try:
        agents = [
            SpaghettiReviewAgent(
                pkg_name,
                PACKAGES[pkg_name],
                config=dataclasses.replace(config, recursive=pkg_name not in non_recursive),
                executor=executor,
                skip_logger=True,
            )
            for pkg_name in pkg_names
        ]
        pairs = await asyncio.gather(*(_review_one(agent) for agent in agents))
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
