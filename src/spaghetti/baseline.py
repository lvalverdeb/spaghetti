"""Baseline file: the findings a project has accepted for now.

A baseline lets a legacy codebase adopt spaghetti with zero new findings on
day one and ratchet down from there. Findings are keyed by their
:attr:`Issue.fingerprint`, so they survive line shifts and unrelated edits.

File shape (``schema_version`` 1)::

    {
      "schema_version": 1,
      "generated_by": "spaghetti 1.5.0",
      "findings": {
        "7ef53d04ad35db1d": {"rule": "long-function", "file": "src/m.py", "message": "..."},
        ...
      }
    }
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from spaghetti.models import Issue, _display_path

BASELINE_SCHEMA_VERSION = 1


class BaselineError(ValueError):
    """The baseline file exists but cannot be used."""


@dataclass(frozen=True)
class BaselineEntry:
    fingerprint: str
    rule: str
    file: str
    message: str


def load_baseline(path: Path) -> dict[str, BaselineEntry]:
    """Fingerprint → entry. A missing file is an empty baseline, not an error."""
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise BaselineError(f"{path}: cannot read baseline — {exc}") from exc
    if not isinstance(data, dict) or data.get("schema_version") != BASELINE_SCHEMA_VERSION:
        raise BaselineError(
            f"{path}: expected a baseline with schema_version {BASELINE_SCHEMA_VERSION}"
        )
    findings = data.get("findings", {})
    if not isinstance(findings, dict):
        raise BaselineError(f"{path}: 'findings' must be an object keyed by fingerprint")
    out: dict[str, BaselineEntry] = {}
    for fp, entry in findings.items():
        if not isinstance(entry, dict):
            raise BaselineError(f"{path}: entry {fp!r} must be an object")
        out[fp] = BaselineEntry(
            fingerprint=fp,
            rule=str(entry.get("rule", "")),
            file=str(entry.get("file", "")),
            message=str(entry.get("message", "")),
        )
    return out


def save_baseline(path: Path, issues: list[Issue], *, generated_by: str) -> None:
    findings = {
        i.fingerprint: {"rule": i.rule, "file": _display_path(i.file), "message": i.message}
        for i in sorted(issues, key=lambda i: i.fingerprint)
    }
    payload = {
        "schema_version": BASELINE_SCHEMA_VERSION,
        "generated_by": generated_by,
        "findings": findings,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=False) + "\n", encoding="utf-8")


@dataclass
class BaselineSplit:
    new: list[Issue]
    """Findings not in the baseline — what the run reports and gates on."""
    known: list[Issue]
    """Findings the baseline accepts — hidden from the report, counted."""
    fixed: list[BaselineEntry]
    """Baseline entries no finding matched any more — the ratchet's progress."""


def split_against_baseline(
    issues: list[Issue], baseline: dict[str, BaselineEntry]
) -> BaselineSplit:
    new: list[Issue] = []
    known: list[Issue] = []
    seen: set[str] = set()
    for issue in issues:
        if issue.fingerprint in baseline:
            known.append(issue)
            seen.add(issue.fingerprint)
        else:
            new.append(issue)
    fixed = [entry for fp, entry in baseline.items() if fp not in seen]
    return BaselineSplit(new=new, known=known, fixed=fixed)
