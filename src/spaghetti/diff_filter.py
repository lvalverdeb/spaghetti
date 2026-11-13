"""Changed-lines mode (``--diff-base REF``): report only findings that touch
lines added or modified relative to a git ref.

This is the "new code" gate: a legacy repo can enforce rules on every pull
request without first paying down the existing backlog. Package-level
findings attributed to an unchanged file (an ``import-cycle`` reported on
``__init__.py``, say) are hidden like any other; the summary says how many
findings the filter hid so that is never silent.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

from spaghetti.models import Issue

_HUNK_RE = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")


class DiffError(RuntimeError):
    """git could not produce the diff (not a repo, unknown ref, git missing)."""


def _git(cwd: Path, *args: str) -> str:
    try:
        proc = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=False)
    except FileNotFoundError as exc:
        raise DiffError("git executable not found") from exc
    if proc.returncode != 0:
        raise DiffError(proc.stderr.strip() or f"git {' '.join(args)} failed")
    return proc.stdout


def changed_lines(base_ref: str, cwd: Path) -> dict[Path, set[int]]:
    """Absolute path → set of 1-based line numbers added or modified since ``base_ref``.

    Compares ``base_ref`` to the working tree (staged and unstaged edits
    included), which is what a pre-commit hook or a PR check wants.
    """
    toplevel = Path(_git(cwd, "rev-parse", "--show-toplevel").strip()).resolve()
    diff = _git(cwd, "diff", "-U0", "--no-color", "--diff-filter=AMR", base_ref, "--")
    return parse_unified_diff(diff, toplevel)


def parse_unified_diff(diff: str, toplevel: Path) -> dict[Path, set[int]]:
    changed: dict[Path, set[int]] = {}
    current: Path | None = None
    for line in diff.splitlines():
        if line.startswith("+++ "):
            target = line[4:]
            if target == "/dev/null":
                current = None
                continue
            if target.startswith("b/"):
                target = target[2:]
            current = (toplevel / target).resolve()
            changed.setdefault(current, set())
        elif current is not None and (m := _HUNK_RE.match(line)):
            start = int(m.group(1))
            count = int(m.group(2)) if m.group(2) is not None else 1
            changed[current].update(range(start, start + count))
    return changed


@dataclass
class DiffSplit:
    kept: list[Issue]
    hidden: int


def restrict_to_changed(issues: list[Issue], changed: dict[Path, set[int]]) -> DiffSplit:
    kept: list[Issue] = []
    for issue in issues:
        lines = changed.get(issue.file.resolve())
        if lines is None:
            continue
        end = issue.end_line if issue.end_line is not None else issue.line
        if any(n in lines for n in range(issue.line, end + 1)):
            kept.append(issue)
    return DiffSplit(kept=kept, hidden=len(issues) - len(kept))
