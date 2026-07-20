"""Inline ``# spaghetti-ignore`` suppression support."""

from __future__ import annotations

from dataclasses import dataclass

from spaghetti.config import SUPPRESS_MARKER_RE
from spaghetti.models import Issue

# A marker applies to the issue's own line, or the line directly above it.
_LOOKBACK_OFFSETS = (1, 2)

# Capture-group indices in SUPPRESS_MARKER_RE: the "[rule1,rule2]" rules list,
# and the ": reason text" suffix.
_RULES_GROUP = 1
_REASON_GROUP = 2


@dataclass(frozen=True)
class Suppression:
    """A parsed ``# spaghetti-ignore[rule1,rule2]: reason`` marker."""

    rules: frozenset[str]  # empty == suppresses every rule on the line
    reason: str | None


def _suppression_at(source_lines: list[str], line_no: int) -> Suppression | None:
    """Suppression marker in effect for 1-indexed *line_no*, or None."""
    for offset in _LOOKBACK_OFFSETS:
        idx = line_no - offset
        if 0 <= idx < len(source_lines):
            match = SUPPRESS_MARKER_RE.search(source_lines[idx])
            if match:
                rules_group, reason_group = match.group(_RULES_GROUP), match.group(_REASON_GROUP)
                rules = (
                    frozenset()
                    if rules_group is None or not rules_group.strip()
                    else frozenset(r.strip() for r in rules_group.split(",") if r.strip())
                )
                reason = reason_group.strip() if reason_group and reason_group.strip() else None
                return Suppression(rules=rules, reason=reason)
    return None


def _suppressed_rules_at(source_lines: list[str], line_no: int) -> set[str] | None:
    """Rules suppressed for 1-indexed *line_no*, or None if no marker applies."""
    sup = _suppression_at(source_lines, line_no)
    return None if sup is None else set(sup.rules)


def _is_suppressed(issue: Issue, source_lines: list[str]) -> Suppression | None:
    """The Suppression covering *issue*, or None if it isn't suppressed."""
    sup = _suppression_at(source_lines, issue.line)
    if sup is None:
        return None
    if sup.rules and issue.rule not in sup.rules:
        return None
    return sup
