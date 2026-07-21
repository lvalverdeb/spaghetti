"""Check registries: ALL_CHECKS, SOURCE_CHECKS, PACKAGE_CHECKS."""

from __future__ import annotations

import ast
from collections.abc import Callable
from pathlib import Path

from spaghetti.checks.ast_per_file import ALL_CHECKS, check_magic_numbers
from spaghetti.checks.package_level import (
    check_duplicate_functions_pkg,
    check_import_cycles_pkg,
    check_orphan_interfaces_pkg,
    check_sync_async_twins_pkg,
)
from spaghetti.checks.text_per_file import check_long_file, check_todo_markers
from spaghetti.models import Issue

__all__ = [
    "ALL_CHECKS",
    "SOURCE_CHECKS",
    "PACKAGE_CHECKS",
    "check_duplicate_functions_pkg",
    "check_magic_numbers",
    "check_orphan_interfaces_pkg",
    "check_sync_async_twins_pkg",
]

SourceCheck = Callable[[str, Path, str], list[Issue]]
PackageCheck = Callable[[str, list[tuple[Path, ast.Module]]], list[Issue]]

SOURCE_CHECKS: list[SourceCheck] = [
    check_long_file,
    check_todo_markers,
]

PACKAGE_CHECKS: list[PackageCheck] = [
    check_import_cycles_pkg,
    check_orphan_interfaces_pkg,
]
