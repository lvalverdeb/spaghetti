"""Inline ``# spaghetti-ignore`` suppression support."""
from __future__ import annotations

from spaghetti.config import SUPPRESS_MARKER_RE
from spaghetti.models import Issue


def _suppressed_rules_at(source_lines: list[str], line_no: int) -> set[str] | None:
    """Rules suppressed for 1-indexed *line_no*, or None if no marker applies."""
    for idx in (line_no - 1, line_no - 2):
        if 0 <= idx < len(source_lines):
            match = SUPPRESS_MARKER_RE.search(source_lines[idx])
            if match:
                rules_group = match.group(1)
                if rules_group is None or not rules_group.strip():
                    return set()
                return {r.strip() for r in rules_group.split(",") if r.strip()}
    return None


def _is_suppressed(issue: Issue, source_lines: list[str]) -> bool:
    rules = _suppressed_rules_at(source_lines, issue.line)
    if rules is None:
        return False
    return not rules or issue.rule in rules
