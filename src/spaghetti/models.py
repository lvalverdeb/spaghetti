"""Core data models for the spaghetti detector."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from spaghetti.checks.workspace_level import RawImport
    from spaghetti.clones import TokenStream

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
    column: int | None = None
    """1-based character column of the finding's start, when the rule knows it."""
    end_line: int | None = None
    end_column: int | None = None
    """1-based character column just past the finding's end (exclusive)."""
    fingerprint: str = ""
    """Stable identity across line shifts and re-runs — see ``detector._assign_fingerprints``."""
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
    min_clone_tokens: int = 50
    cache_dir: Path | None = None
    """Root of the incremental per-file cache (see ``spaghetti.cache``); ``None`` disables it."""
    cache_key: str = ""


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

    file_mi: dict[Path, float] = field(default_factory=dict)
    """Maintainability Index per parsed file (see ``metrics.maintainability_index``)."""
    cache_hits: int = 0
    cache_misses: int = 0
    raw_imports: dict[Path, list[RawImport]] = field(default_factory=dict)
    """Transport from worker to parent for the workspace-level import graph; cleared after use."""
    token_streams: dict[Path, TokenStream] = field(default_factory=dict)
    """Transport from worker to parent for clone detection; cleared after use."""

    def extend(self, other: ScanResult) -> None:
        """Fold ``other`` into this result (the all-packages total)."""
        self.issues.extend(other.issues)
        self.files_scanned += other.files_scanned
        self.functions_scanned += other.functions_scanned
        self.total_lines += other.total_lines
        self.suppressed += other.suppressed
        self.ignored.extend(other.ignored)
        self.file_mi.update(other.file_mi)
        self.cache_hits += other.cache_hits
        self.cache_misses += other.cache_misses

    @property
    def maintainability_index(self) -> float | None:
        """Mean MI over parsed files, or ``None`` when nothing parsed."""
        if not self.file_mi:
            return None
        return sum(self.file_mi.values()) / len(self.file_mi)

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
