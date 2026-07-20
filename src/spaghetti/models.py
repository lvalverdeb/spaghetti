"""Core data models for the spaghetti detector."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

Severity = Literal["info", "warning", "error"]


def _display_path(path: Path) -> str:
    """Workspace-relative path when possible, absolute otherwise."""
    from spaghetti.config import WORKSPACE_ROOT

    if WORKSPACE_ROOT is None:
        return str(path)
    try:
        return str(path.relative_to(WORKSPACE_ROOT))
    except ValueError:
        return str(path)


@dataclass
class Issue:
    file: Path
    line: int
    severity: Severity
    rule: str
    message: str
    package: str = ""
    reason: str | None = None
    """Human-supplied justification from a ``# spaghetti-ignore: ...`` marker.

    Only ever set on issues that ended up in :attr:`ScanResult.ignored`.
    """

    def __str__(self) -> str:
        icon = {"info": "ℹ", "warning": "⚠", "error": "✖"}[self.severity]
        return f"  {icon} {_display_path(self.file)}:{self.line} [{self.rule}] {self.message}"


@dataclass
class ScanConfig:
    """Parameter Object bundling `scan_package`'s tunable knobs.

    Extracted so ``scan_package``, ``SpaghettiReviewAgent.__init__``, and
    ``review_packages_concurrently`` share one shape instead of the same
    three-or-four values threaded through each of their signatures in
    parallel.
    """

    exclude: list[str]
    min_duplicate_lines: int
    twin_similarity: float
    recursive: bool = True


@dataclass
class ScanResult:
    issues: list[Issue] = field(default_factory=list)
    files_scanned: int = 0
    functions_scanned: int = 0
    total_lines: int = 0
    suppressed: int = 0
    ignored: list[Issue] = field(default_factory=list)
    """Issues suppressed by an inline ``# spaghetti-ignore`` marker.

    Kept (rather than discarded) so callers can audit *why* something was
    waived — each entry's ``reason`` is the text after the marker's ``:``,
    or ``None`` if the marker didn't give one.
    """

    @property
    def error_count(self) -> int:
        return sum(1 for i in self.issues if i.severity == "error")

    @property
    def warning_count(self) -> int:
        return sum(1 for i in self.issues if i.severity == "warning")

    @property
    def info_count(self) -> int:
        return sum(1 for i in self.issues if i.severity == "info")


@dataclass
class RemediationStep:
    """A single prioritized remediation action."""

    priority: str
    rule: str
    severity: str
    effort: str
    files: list[str]
    count: int
    description: str
    score: float
