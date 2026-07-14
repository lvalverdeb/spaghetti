"""Core data models for the spaghetti detector."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

Severity = Literal["info", "warning", "error"]


def _display_path(path: Path) -> str:
    """Workspace-relative path when possible, absolute otherwise."""
    from spaghetti.config import WORKSPACE_ROOT

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

    def __str__(self) -> str:
        icon = {"info": "ℹ", "warning": "⚠", "error": "✖"}[self.severity]
        return f"  {icon} {_display_path(self.file)}:{self.line} [{self.rule}] {self.message}"


@dataclass
class ScanResult:
    issues: list[Issue] = field(default_factory=list)
    files_scanned: int = 0
    functions_scanned: int = 0
    total_lines: int = 0
    suppressed: int = 0

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
