"""Tests for the self-contained HTML report."""

from __future__ import annotations

from pathlib import Path

from spaghetti.churn import Hotspot
from spaghetti.html_report import build_html
from spaghetti.models import Issue, ScanResult
from spaghetti.snapshots import Snapshot, compare, take_snapshot


def _issue(msg: str, *, fp: str) -> Issue:
    return Issue(
        file=Path("/w/pkg/m.py"),
        line=3,
        severity="warning",
        rule="long-function",
        message=msg,
        package="pkg",
        fingerprint=fp,
    )


def test_build_html_is_self_contained_and_escapes_content():
    issues = [_issue("<script>alert(1)</script> & co", fp="x1")]
    result = ScanResult(issues=issues, files_scanned=1, total_lines=500)
    history = [
        Snapshot(
            timestamp=f"2026-11-{d:02d}T00:00:00+00:00",
            sha=None,
            tool_version="t",
            scores={"pkg": 70.0 + d, "*": 70.0 + d},
            files={},
        )
        for d in range(1, 6)
    ]
    current = take_snapshot({"pkg": result}, result, tool_version="t", sha="deadbeefcafe")
    comp = compare(history[-1], current, issues)
    html = build_html(
        packages=["pkg"],
        per_package={"pkg": result},
        total=result,
        issues=issues,
        tool_version="2.0.0",
        sha="deadbeefcafe",
        generated_at="2026-11-13T12:00:00+00:00",
        notes=["a <note>"],
        history=history,
        comparison=comp,
        hotspots=[Hotspot("pkg/m.py", 1.5, 4, 2, 2.4)],
        hotspot_months=6,
        top=5,
    )
    assert html.startswith("<!DOCTYPE html>") and "<script" not in html.replace("&lt;script", "")
    assert "&lt;script&gt;alert(1)&lt;/script&gt; &amp; co" in html
    assert "a &lt;note&gt;" in html
    assert 'aria-label="6 snapshot(s)"' in html  # 5 stored + the current run
    assert "<h2>Change</h2>" in html and "Introduced" in html
    assert "<h2>Hotspots</h2>" in html and "pkg/m.py" in html
    assert "href=" not in html and "src=" not in html  # no external assets
