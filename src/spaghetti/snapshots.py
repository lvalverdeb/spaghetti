"""Snapshot store and run-to-run comparison (ROADMAP Phase 3).

``spaghetti --snapshot`` appends one JSON line per run to a local store
(default ``.spaghetti/snapshots.jsonl``): git SHA, timestamp, per-package
scores, per-file penalties and every finding's fingerprint. ``--compare REF``
diffs the current run against a stored one — score deltas, findings
introduced and fixed, files that got worse — so a PR check can say exactly
what a change did to the codebase's health.
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from spaghetti.models import Issue, ScanResult, _display_path
from spaghetti.scoring import compute_score, file_penalties

__all__ = [
    "Comparison",
    "Snapshot",
    "SnapshotError",
    "SnapshotFinding",
    "append_snapshot",
    "compare",
    "find_snapshot",
    "load_snapshots",
    "take_snapshot",
]

SNAPSHOT_SCHEMA_VERSION = 1
DEFAULT_STORE = Path(".spaghetti/snapshots.jsonl")


class SnapshotError(ValueError):
    """The store or a reference into it is unusable; the message says why."""


@dataclass(frozen=True)
class SnapshotFinding:
    fingerprint: str
    rule: str
    severity: str
    file: str
    line: int
    message: str

    @classmethod
    def from_issue(cls, issue: Issue) -> SnapshotFinding:
        return cls(
            issue.fingerprint,
            issue.rule,
            issue.severity,
            _display_path(issue.file),
            issue.line,
            issue.message,
        )

    def to_json(self) -> dict[str, object]:
        return {
            "fingerprint": self.fingerprint,
            "rule": self.rule,
            "severity": self.severity,
            "file": self.file,
            "line": self.line,
            "message": self.message,
        }


@dataclass
class Snapshot:
    timestamp: str
    sha: str | None
    tool_version: str
    scores: dict[str, float]
    """Package name -> health score; ``"*"`` is the overall score."""
    files: dict[str, float]
    """Display path -> penalty (sum of severity weights of its findings)."""
    findings: list[SnapshotFinding] = field(default_factory=list)

    @property
    def label(self) -> str:
        return f"{self.sha[:12] if self.sha else 'no-sha'} @ {self.timestamp}"

    def to_json(self) -> dict[str, object]:
        return {
            "schema_version": SNAPSHOT_SCHEMA_VERSION,
            "timestamp": self.timestamp,
            "sha": self.sha,
            "tool_version": self.tool_version,
            "scores": self.scores,
            "files": self.files,
            "findings": [f.to_json() for f in self.findings],
        }

    @classmethod
    def from_json(cls, data: object, *, origin: str) -> Snapshot:
        if not isinstance(data, dict):
            raise SnapshotError(f"{origin}: malformed snapshot (not an object)")
        if data.get("schema_version") != SNAPSHOT_SCHEMA_VERSION:
            raise SnapshotError(
                f"{origin}: unsupported snapshot schema_version {data.get('schema_version')!r}"
            )
        try:
            findings = [SnapshotFinding(**f) for f in data["findings"]]  # type: ignore[union-attr]
            return cls(
                timestamp=str(data["timestamp"]),
                sha=data.get("sha"),  # type: ignore[arg-type]
                tool_version=str(data.get("tool_version", "")),
                scores={k: float(v) for k, v in data["scores"].items()},  # type: ignore[union-attr]
                files={k: float(v) for k, v in data["files"].items()},  # type: ignore[union-attr]
                findings=findings,
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise SnapshotError(f"{origin}: malformed snapshot ({exc})") from exc


def git_head_sha(cwd: Path) -> str | None:
    try:
        proc = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=cwd, capture_output=True, text=True, check=False
        )
    except FileNotFoundError:
        return None
    return proc.stdout.strip() or None if proc.returncode == 0 else None


def take_snapshot(
    per_package: dict[str, ScanResult],
    total: ScanResult,
    *,
    tool_version: str,
    sha: str | None,
    now: datetime | None = None,
) -> Snapshot:
    stamp = (now or datetime.now(UTC)).replace(microsecond=0).isoformat()
    scores = {name: round(compute_score(r)[0], 1) for name, r in per_package.items()}
    scores["*"] = round(compute_score(total)[0], 1)
    files = {_display_path(path): round(p, 2) for path, p in file_penalties(total.issues).items()}
    return Snapshot(
        timestamp=stamp,
        sha=sha,
        tool_version=tool_version,
        scores=scores,
        files=files,
        findings=[SnapshotFinding.from_issue(i) for i in total.issues],
    )


def append_snapshot(store: Path, snapshot: Snapshot) -> None:
    store.parent.mkdir(parents=True, exist_ok=True)
    with store.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(snapshot.to_json(), ensure_ascii=False) + "\n")


def load_snapshots(store: Path) -> list[Snapshot]:
    """Every snapshot in the store, oldest first; an absent store is empty."""
    if not store.exists():
        return []
    snapshots: list[Snapshot] = []
    # Split on "\n" only: the store is written with ensure_ascii=False, and
    # str.splitlines() would also break lines at U+2028/U+2029/U+0085 inside
    # a path or message.
    for number, line in enumerate(store.read_text(encoding="utf-8").split("\n"), start=1):
        if not line.strip():
            continue
        try:
            data = json.loads(line)
        except json.JSONDecodeError as exc:
            raise SnapshotError(f"{store}:{number}: not JSON ({exc.msg})") from exc
        snapshots.append(Snapshot.from_json(data, origin=f"{store}:{number}"))
    return snapshots


def find_snapshot(snapshots: list[Snapshot], ref: str) -> Snapshot:
    """``latest``; ``~N`` for the N-th most recent (``~1`` = the one before
    latest); a git SHA prefix; or a timestamp prefix (``2026-11``)."""
    if not snapshots:
        raise SnapshotError("the snapshot store is empty — run with --snapshot first")
    if ref == "latest":
        return snapshots[-1]
    if ref.startswith("~") and ref[1:].isdigit():
        back = int(ref[1:])
        if back >= len(snapshots):
            raise SnapshotError(f"only {len(snapshots)} snapshot(s) stored; {ref} is out of range")
        return snapshots[-1 - back]
    by_sha = [s for s in snapshots if s.sha and s.sha.startswith(ref)]
    if by_sha:
        return by_sha[-1]
    by_time = [s for s in snapshots if s.timestamp.startswith(ref)]
    if by_time:
        return by_time[-1]
    raise SnapshotError(
        f"no snapshot matches {ref!r} (use latest, ~N, a SHA prefix or a timestamp prefix)"
    )


@dataclass
class Comparison:
    against: Snapshot
    score_before: float
    score_after: float
    packages: dict[str, tuple[float | None, float]]
    """name -> (score before or None if the package is new, score after)."""
    introduced: list[Issue]
    fixed: list[SnapshotFinding]
    files_worse: list[tuple[str, float, float]]
    """(display path, penalty before, penalty after) where it increased, worst first."""
    files_better: list[tuple[str, float, float]]

    @property
    def score_delta(self) -> float:
        return round(self.score_after - self.score_before, 1)

    def notes(self) -> list[str]:
        sign = "+" if self.score_delta >= 0 else ""
        return [
            f"Compared to {self.against.label}: score {self.score_before} → {self.score_after} "
            f"({sign}{self.score_delta}), {len(self.introduced)} finding(s) introduced, "
            f"{len(self.fixed)} fixed, {len(self.files_worse)} file(s) worse"
        ]

    def to_json(self) -> dict[str, object]:
        return {
            "against": {"sha": self.against.sha, "timestamp": self.against.timestamp},
            "score_before": self.score_before,
            "score_after": self.score_after,
            "score_delta": self.score_delta,
            "packages": {
                name: {"before": before, "after": after}
                for name, (before, after) in self.packages.items()
            },
            "introduced": [SnapshotFinding.from_issue(i).to_json() for i in self.introduced],
            "fixed": [f.to_json() for f in self.fixed],
            "files_worse": [{"file": f, "before": b, "after": a} for f, b, a in self.files_worse],
            "files_better": [{"file": f, "before": b, "after": a} for f, b, a in self.files_better],
        }


def compare(against: Snapshot, current: Snapshot, current_issues: list[Issue]) -> Comparison:
    """What changed between ``against`` and the current run. Findings are
    matched by fingerprint, so a finding that merely moved lines is neither
    introduced nor fixed."""
    old_fps = {f.fingerprint for f in against.findings}
    new_fps = {f.fingerprint for f in current.findings}
    introduced = [i for i in current_issues if i.fingerprint not in old_fps]
    fixed = [f for f in against.findings if f.fingerprint not in new_fps]
    packages = {
        name: (against.scores.get(name), score)
        for name, score in current.scores.items()
        if name != "*"
    }
    worse: list[tuple[str, float, float]] = []
    better: list[tuple[str, float, float]] = []
    for path in sorted(set(against.files) | set(current.files)):
        before = against.files.get(path, 0.0)
        after = current.files.get(path, 0.0)
        if after > before:
            worse.append((path, before, after))
        elif after < before:
            better.append((path, before, after))
    worse.sort(key=lambda t: (-(t[2] - t[1]), t[0]))
    better.sort(key=lambda t: (-(t[1] - t[2]), t[0]))
    return Comparison(
        against=against,
        score_before=against.scores.get("*", 0.0),
        score_after=current.scores["*"],
        packages=packages,
        introduced=introduced,
        fixed=fixed,
        files_worse=worse,
        files_better=better,
    )
