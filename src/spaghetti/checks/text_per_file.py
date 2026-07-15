"""Text-based (non-AST) per-file checks."""

from __future__ import annotations

from pathlib import Path

from spaghetti.config import MAX_FILE_LINES, TODO_RE
from spaghetti.models import Issue

__all__ = ["check_long_file", "check_todo_markers"]


def check_long_file(source: str, filepath: Path, pkg: str) -> list[Issue]:
    count = len(source.splitlines())
    if count > MAX_FILE_LINES:
        return [
            Issue(
                file=filepath,
                line=1,
                severity="warning",
                rule="long-file",
                package=pkg,
                message=f"File is {count} lines (max {MAX_FILE_LINES})",
            )
        ]
    return []


def check_todo_markers(source: str, filepath: Path, pkg: str) -> list[Issue]:
    """Flags TODO/FIXME/XXX/HACK comments — not spaghetti by themselves, but a
    high concentration correlates with unfinished or known-fragile code."""
    issues: list[Issue] = []
    for i, line in enumerate(source.splitlines(), 1):
        match = TODO_RE.search(line)
        if match:
            snippet = line.strip()
            if len(snippet) > 90:
                snippet = snippet[:87] + "..."
            issues.append(
                Issue(
                    file=filepath,
                    line=i,
                    severity="info",
                    rule="todo-marker",
                    package=pkg,
                    message=f"{match.group(1)} marker: {snippet}",
                )
            )
    return issues
