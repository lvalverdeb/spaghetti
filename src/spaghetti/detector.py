#!/usr/bin/env python3
"""Spaghetti Code Detection Script.

Thin re-export shim that preserves backward compatibility with tests
that do ``from spaghetti import detector as ds`` and monkeypatch
``ds.PACKAGES``, ``ds.scan_package``, etc.

All actual logic lives in:
  - spaghetti.config          (workspace root, thresholds, layer rules)
  - spaghetti.models          (Issue, ScanResult, RemediationStep)
  - spaghetti.suppression     (inline spaghetti-ignore markers)
  - spaghetti.ast_helpers     (AST utility functions)
  - spaghetti.checks.*        (36 check functions)
  - spaghetti.scoring         (health score, remediation plan)
  - spaghetti.cli             (CLI entry point, resolve_packages)
"""

from __future__ import annotations

import ast
import asyncio
import concurrent.futures
import sys
from functools import partial
from pathlib import Path
from typing import Any

from boti.core import Agent

# ── Re-export ast_helpers ─────────────────────────────────────────────────────
from spaghetti.ast_helpers import (  # noqa: F401
    _count_own_returns,
    _cyclomatic_complexity,
    _dump_stmts,
    _file_line_count,
    _has_return_type_hint,
    _is_private,
    _is_trivial_body,
    _line_count,
    _nesting_depth,
    _param_has_type_hint,
    _walk_with_class_context,
)

# ── Re-export check registries ────────────────────────────────────────────────
from spaghetti.checks import ALL_CHECKS, PACKAGE_CHECKS, SOURCE_CHECKS  # noqa: F401

# ── Re-export ALL check functions ─────────────────────────────────────────────
from spaghetti.checks.ast_per_file import (  # noqa: F401
    check_bare_except,
    check_boolean_flag_params,
    check_circular_imports,
    check_complexity,
    check_dead_code,
    check_deep_inheritance,
    check_deep_nesting,
    check_duplicate_branches,
    check_encapsulation_violations,
    check_excessive_decorators,
    check_excessive_params,
    check_excessive_returns,
    check_global_mutations,
    check_god_class,
    check_god_module,
    check_layer_violations,
    check_lazy_class,
    check_long_functions,
    check_magic_numbers,
    check_message_chains,
    check_missing_else,
    check_missing_types,
    check_mutable_defaults,
    check_pass_through_methods,
    check_scope_mutations,
    check_star_imports,
    check_swallowed_exceptions,
    check_transport_in_library,
    check_untyped_dicts,
    check_unused_imports,
)
from spaghetti.checks.package_level import (  # noqa: F401  # spaghetti-ignore[unused-import]: re-exported for backward compat
    check_duplicate_functions_pkg,
    check_import_cycles_pkg,
    check_orphan_interfaces_pkg,
    check_sync_async_twins_pkg,
)
from spaghetti.checks.text_per_file import (  # noqa: F401
    check_long_file,
    check_todo_markers,
)

# ── Re-export CLI helpers ─────────────────────────────────────────────────────
from spaghetti.cli import (  # noqa: F401
    _load_packages_from_config,
    _parse_package_args,
    resolve_packages,
)

# ── Re-export config constants ────────────────────────────────────────────────
from spaghetti.config import (  # noqa: F401
    COMPLEXITY_THRESHOLD,
    DEFAULT_MIN_DUPLICATE_LINES,
    DEFAULT_TWIN_SIMILARITY,
    DUNDER_RE,
    LAYER_RULES,
    MAX_CLASS_ATTRS,
    MAX_CLASS_METHODS,
    MAX_CROSS_LAYER_IMPORTS,
    MAX_DECORATORS,
    MAX_FILE_LINES,
    MAX_FUNC_PARAMS,
    MAX_FUNCTION_LINES,
    MAX_INHERITANCE_DEPTH,
    MAX_MESSAGE_CHAIN_DEPTH,
    MAX_NESTING_DEPTH,
    MAX_RETURNS,
    MIN_BOOLEAN_FLAGS,
    SUPPRESS_MARKER_RE,
    TODO_RE,
    WORKSPACE_ROOT,
    _find_workspace_root,
)
from spaghetti.config import DEFAULT_PACKAGES as _DEFAULT_PACKAGES

# ── Re-export models ──────────────────────────────────────────────────────────
from spaghetti.models import (  # noqa: F401
    Issue,
    RemediationStep,
    ScanResult,
    Severity,
    _display_path,
)

# ── Re-export scoring ─────────────────────────────────────────────────────────
from spaghetti.scoring import (  # noqa: F401
    build_remediation_plan,
    compute_priority_score,
    compute_score,
    plan_report,
)

# ── Re-export suppression helpers ─────────────────────────────────────────────
from spaghetti.suppression import (  # noqa: F401
    _is_suppressed,
    _suppressed_rules_at,
)

# ══════════════════════════════════════════════════════════════════════════════
# Items that MUST remain in detector.py for test monkeypatch compatibility:
#   - PACKAGES (tests: monkeypatch.setattr(ds, "PACKAGES", ...))
#   - ALLOWED_IMPORT_PREFIXES (tests: ds.ALLOWED_IMPORT_PREFIXES[pkg_name] = ...)
#   - scan_package (tests: monkeypatch.setattr(ds, "scan_package", fake))
#   - SpaghettiReviewAgent (tests: ds.SpaghettiReviewAgent(...))
#   - review_packages_concurrently (tests: ds.review_packages_concurrently(...))
# ══════════════════════════════════════════════════════════════════════════════

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


def scan_package(
    pkg_name: str,
    pkg_path: Path,
    *,
    exclude: list[str],
    min_duplicate_lines: int,
    twin_similarity: float,
) -> ScanResult:
    result = ScanResult()
    if not pkg_path.exists():
        return result

    parsed_files: list[tuple[Path, ast.Module]] = []
    source_lines_by_file: dict[Path, list[str]] = {}

    for py_file in sorted(pkg_path.rglob("*.py")):
        path_str = str(py_file)
        if "__pycache__" in path_str:
            continue
        if any(pattern in path_str for pattern in exclude):
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
    result.issues.extend(check_duplicate_functions_pkg(pkg_name, parsed_files, min_duplicate_lines))
    result.issues.extend(check_sync_async_twins_pkg(pkg_name, parsed_files, twin_similarity))

    kept: list[Issue] = []
    for issue in result.issues:
        lines = source_lines_by_file.get(issue.file)
        if lines is not None and _is_suppressed(issue, lines):
            result.suppressed += 1
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
        exclude: list[str],
        min_duplicate_lines: int,
        twin_similarity: float,
        executor: concurrent.futures.ProcessPoolExecutor,
        **agent_kwargs: Any,
    ) -> None:
        super().__init__(**agent_kwargs)
        self.pkg_name = pkg_name
        self.pkg_path = pkg_path
        self.exclude = exclude
        self.min_duplicate_lines = min_duplicate_lines
        self.twin_similarity = twin_similarity
        self.executor = executor
        self.result: ScanResult | None = None

    async def review(self) -> ScanResult:
        """Run the package scan in a worker process and record the result."""
        self._assert_open()
        loop = asyncio.get_running_loop()

        func = partial(
            scan_package,
            self.pkg_name,
            self.pkg_path,
            exclude=self.exclude,
            min_duplicate_lines=self.min_duplicate_lines,
            twin_similarity=self.twin_similarity,
        )

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
    exclude: list[str],
    min_duplicate_lines: int,
    twin_similarity: float,
    executor: concurrent.futures.Executor | None = None,
) -> dict[str, ScanResult]:
    """Review every requested package at once using concurrent execution."""
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
                exclude=exclude,
                min_duplicate_lines=min_duplicate_lines,
                twin_similarity=twin_similarity,
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
