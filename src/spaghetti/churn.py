"""Change frequency from git history, and the health × churn hotspot ranking
(CodeScene's core idea). A file that is both tangled and actively edited is
where remediation effort pays off; a tangled file nobody touches can wait."""

from __future__ import annotations

import math
import subprocess
from dataclasses import dataclass
from pathlib import Path

__all__ = ["Churn", "ChurnError", "Hotspot", "churn_factor", "file_churn", "rank_hotspots"]


class ChurnError(RuntimeError):
    """git is missing, the path is not a repository, or git failed."""


@dataclass(frozen=True)
class Churn:
    commits: int
    authors: int


def _git(cwd: Path, *args: str) -> str:
    try:
        # core.quotePath=false: otherwise non-ASCII paths come back quoted and
        # octal-escaped and never match the scanned files.
        proc = subprocess.run(
            ["git", "-c", "core.quotePath=false", *args],
            cwd=cwd,
            capture_output=True,
            text=True,
            check=False,
        )
    except FileNotFoundError as exc:
        raise ChurnError("git executable not found") from exc
    if proc.returncode != 0:
        raise ChurnError(proc.stderr.strip() or f"git {' '.join(args)} failed")
    return proc.stdout


def file_churn(roots: list[Path], *, months: int) -> dict[Path, Churn]:
    """Commits and distinct authors touching each file under ``roots`` in the
    last ``months`` months. Keys are resolved absolute paths. Every root must
    be inside one git work tree."""
    if not roots:
        return {}
    toplevel = Path(_git(roots[0], "rev-parse", "--show-toplevel").strip()).resolve()
    log = _git(
        toplevel,
        "log",
        f"--since={months} months ago",
        "--name-only",
        "--format=%x00%an",
        "--",
        *[str(r.resolve()) for r in roots],
    )
    commits: dict[Path, int] = {}
    authors: dict[Path, set[str]] = {}
    author = ""
    for line in log.splitlines():
        if line.startswith("\x00"):
            author = line[1:]
            continue
        if not line.strip():
            continue
        path = (toplevel / line).resolve()
        commits[path] = commits.get(path, 0) + 1
        authors.setdefault(path, set()).add(author)
    return {p: Churn(commits[p], len(authors[p])) for p in commits}


def churn_factor(churn: Churn | None, *, max_commits: int) -> float:
    """Multiplier in [1, 2] applied to a finding's priority: 1 for an untouched
    file, 2 for the most-edited file in the window, log-scaled in between so a
    handful of commits already counts and the long tail is compressed.
    Relative to the repository's own maximum, so an active codebase does not
    see every finding promoted to P0."""
    if churn is None or churn.commits <= 0 or max_commits <= 0:
        return 1.0
    return 1.0 + math.log2(1 + churn.commits) / math.log2(1 + max_commits)


@dataclass(frozen=True)
class Hotspot:
    file: str
    penalty: float
    commits: int
    authors: int
    score: float
    """penalty × churn_factor — the health × change-frequency product."""


def rank_hotspots(
    penalties: dict[Path, float], churn: dict[Path, Churn], *, display: dict[Path, str]
) -> list[Hotspot]:
    """Files with findings, ranked by penalty × churn factor. Files with no
    findings are never hotspots however often they change."""
    max_commits = max((c.commits for c in churn.values()), default=0)
    spots = [
        Hotspot(
            file=display[path],
            penalty=round(penalty, 1),
            commits=churn.get(path, Churn(0, 0)).commits,
            authors=churn.get(path, Churn(0, 0)).authors,
            score=round(penalty * churn_factor(churn.get(path), max_commits=max_commits), 1),
        )
        for path, penalty in penalties.items()
        if penalty > 0
    ]
    spots.sort(key=lambda h: (-h.score, -h.penalty, h.file))
    return spots
