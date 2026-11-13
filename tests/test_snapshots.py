"""Tests for spaghetti.snapshots: store round-trip, references, comparison."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from spaghetti.models import Issue, ScanResult
from spaghetti.snapshots import (
    Snapshot,
    SnapshotError,
    append_snapshot,
    compare,
    find_snapshot,
    load_snapshots,
    take_snapshot,
)


def _issue(
    fp: str, *, severity: str = "warning", file: str = "/w/pkg/m.py", line: int = 1
) -> Issue:
    return Issue(
        file=Path(file),
        line=line,
        severity=severity,  # type: ignore[arg-type]
        rule="long-function",
        message=f"f() is long {fp}",
        package="pkg",
        fingerprint=fp,
    )


def _result(*issues: Issue, lines: int = 1000) -> ScanResult:
    return ScanResult(issues=list(issues), files_scanned=1, total_lines=lines)


def _snap(
    *issues: Issue, sha: str | None = "abc123def456", when: str = "2026-11-13T10:00:00"
) -> Snapshot:
    result = _result(*issues)
    return take_snapshot(
        {"pkg": result},
        result,
        tool_version="t",
        sha=sha,
        now=datetime.fromisoformat(when).replace(tzinfo=UTC),
    )


def test_take_snapshot_records_scores_files_and_findings():
    snap = _snap(_issue("aaaa"), _issue("bbbb", severity="error", file="/w/pkg/n.py"))
    assert snap.timestamp == "2026-11-13T10:00:00+00:00"
    assert set(snap.scores) == {"pkg", "*"} and snap.scores["pkg"] == snap.scores["*"]
    assert snap.files == {"/w/pkg/m.py": 1.5, "/w/pkg/n.py": 6.0}
    assert [f.fingerprint for f in snap.findings] == ["aaaa", "bbbb"]


def test_store_round_trip_and_references(tmp_path: Path):
    store = tmp_path / "nested" / "snapshots.jsonl"
    assert load_snapshots(store) == []
    first = _snap(_issue("aaaa"), sha="1111aaaa", when="2026-11-01T00:00:00")
    second = _snap(_issue("aaaa"), _issue("bbbb"), sha="2222bbbb", when="2026-11-02T00:00:00")
    append_snapshot(store, first)
    append_snapshot(store, second)
    loaded = load_snapshots(store)
    assert loaded == [first, second]
    assert find_snapshot(loaded, "latest") is loaded[1]
    assert find_snapshot(loaded, "~1") is loaded[0]
    assert find_snapshot(loaded, "1111") is loaded[0]
    assert find_snapshot(loaded, "2026-11-02") is loaded[1]
    with pytest.raises(SnapshotError, match="no snapshot matches"):
        find_snapshot(loaded, "zzz")
    with pytest.raises(SnapshotError, match="out of range"):
        find_snapshot(loaded, "~2")
    with pytest.raises(SnapshotError, match="empty"):
        find_snapshot([], "latest")


def test_unusable_store_lines_raise(tmp_path: Path):
    store = tmp_path / "s.jsonl"
    store.write_text("not json\n")
    with pytest.raises(SnapshotError, match="not JSON"):
        load_snapshots(store)
    store.write_text('{"schema_version": 7}\n')
    with pytest.raises(SnapshotError, match="schema_version"):
        load_snapshots(store)


def test_compare_reports_introduced_fixed_and_files_moved():
    before = _snap(_issue("keep"), _issue("gone", severity="error", file="/w/pkg/old.py"))
    now_issues = [_issue("keep"), _issue("new", file="/w/pkg/new.py", line=9)]
    after = _snap(*now_issues, sha="ffff")
    comp = compare(before, after, now_issues)
    assert [i.fingerprint for i in comp.introduced] == ["new"]
    assert [f.fingerprint for f in comp.fixed] == ["gone"]
    assert comp.files_worse == [("/w/pkg/new.py", 0.0, 1.5)]
    assert comp.files_better == [("/w/pkg/old.py", 6.0, 0.0)]
    assert comp.score_delta == round(after.scores["*"] - before.scores["*"], 1) > 0
    assert comp.packages == {"pkg": (before.scores["pkg"], after.scores["pkg"])}
    js = comp.to_json()
    assert js["against"] == {"sha": "abc123def456", "timestamp": "2026-11-13T10:00:00+00:00"}
    assert js["introduced"][0]["line"] == 9 and js["fixed"][0]["fingerprint"] == "gone"
    assert "introduced" in comp.notes()[0] and "+" in comp.notes()[0]
