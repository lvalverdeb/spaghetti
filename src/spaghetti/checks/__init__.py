"""Check registries: ALL_CHECKS, SOURCE_CHECKS, PACKAGE_CHECKS."""

from __future__ import annotations

import ast
from collections.abc import Callable
from pathlib import Path

from spaghetti.checks.ast_per_file import (
    ALL_CHECKS,
    check_magic_numbers,
    check_swallowed_exceptions,
)
from spaghetti.checks.package_level import (
    check_duplicate_functions_pkg,
    check_orphan_interfaces_pkg,
    check_sync_async_twins_pkg,
)
from spaghetti.checks.text_per_file import check_long_file, check_todo_markers
from spaghetti.models import Issue

__all__ = [
    "ALL_CHECKS",
    "SOURCE_CHECKS",
    "SOURCE_AWARE_CHECKS",
    "PACKAGE_CHECKS",
    "check_duplicate_functions_pkg",
    "check_magic_numbers",
    "check_orphan_interfaces_pkg",
    "check_sync_async_twins_pkg",
]

SourceCheck = Callable[[str, Path, str], list[Issue]]
SourceAwareCheck = Callable[[ast.Module, str, Path, str], list[Issue]]
"""AST checks that also need the raw source (comments, literal spelling)."""
PackageCheck = Callable[[str, list[tuple[Path, ast.Module]]], list[Issue]]

SOURCE_CHECKS: list[SourceCheck] = [
    check_long_file,
    check_todo_markers,
]

SOURCE_AWARE_CHECKS: list[SourceAwareCheck] = [
    check_swallowed_exceptions,
    check_magic_numbers,
]

PACKAGE_CHECKS: list[PackageCheck] = [
    check_orphan_interfaces_pkg,
]
"""Import cycles, coupling and clones run over every package at once — see
``spaghetti.checks.workspace_level`` and ``detector.scan_workspace``."""
