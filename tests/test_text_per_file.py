"""Tests for the per-file source-text checks in spaghetti.checks.text_per_file."""

from __future__ import annotations

from pathlib import Path

from spaghetti.checks.text_per_file import check_long_file, check_todo_markers
from spaghetti.config import MAX_FILE_LINES

# ── Representative check functions ──────────────────────────────────────────


def test_check_long_file_flags_over_threshold():
    source = "\n".join(f"x = {i}" for i in range(MAX_FILE_LINES + 10))
    issues = check_long_file(source, Path("f.py"), "pkg")
    assert len(issues) == 1
    assert issues[0].rule == "long-file"


def test_check_todo_markers_flags_todo_comment():
    source = "x = 1  # TODO: fix this later\n"
    issues = check_todo_markers(source, Path("f.py"), "pkg")
    assert len(issues) == 1
    assert issues[0].rule == "todo-marker"
