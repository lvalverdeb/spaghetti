"""Tests for spaghetti.sarif."""

from __future__ import annotations

from pathlib import Path

from spaghetti.models import Issue
from spaghetti.rules import DEFAULT_RULE_IDS
from spaghetti.sarif import FINGERPRINT_KEY, build_sarif


def _issue(root: Path, **kw) -> Issue:
    base = dict(
        file=root / "src" / "m.py",
        line=3,
        column=5,
        end_line=4,
        end_column=9,
        severity="warning",
        rule="long-function",
        message="f() is long",
        package="pkg",
        fingerprint="0123456789abcdef",
    )
    base.update(kw)
    return Issue(**base)


def test_build_sarif_structure(tmp_path: Path):
    doc = build_sarif(
        new=[_issue(tmp_path)],
        known=[
            _issue(tmp_path, fingerprint="1111111111111111", rule="god-class", severity="error")
        ],
        ignored=[_issue(tmp_path, fingerprint="2222222222222222", severity="info", reason="ok")],
        tool_version="9.9.9",
        root=tmp_path,
        baseline_used=True,
    )
    assert doc["version"] == "2.1.0"
    run = doc["runs"][0]
    driver = run["tool"]["driver"]
    assert driver["name"] == "spaghetti"
    assert driver["version"] == "9.9.9"
    assert {r["id"] for r in driver["rules"]} == set(DEFAULT_RULE_IDS)
    assert run["originalUriBaseIds"]["%SRCROOT%"]["uri"].endswith("/")

    new, known, ignored = run["results"]
    loc = new["locations"][0]["physicalLocation"]
    assert loc["artifactLocation"] == {"uri": "src/m.py", "uriBaseId": "%SRCROOT%"}
    assert loc["region"] == {"startLine": 3, "startColumn": 5, "endLine": 4, "endColumn": 9}
    assert new["level"] == "warning"
    assert new["baselineState"] == "new"
    assert new["partialFingerprints"] == {FINGERPRINT_KEY: "0123456789abcdef"}
    assert new["properties"] == {"package": "pkg"}

    assert known["level"] == "error"
    assert known["baselineState"] == "unchanged"

    assert ignored["level"] == "note"
    assert ignored["suppressions"] == [{"kind": "inSource", "justification": "ok"}]
    assert "baselineState" not in ignored


def test_build_sarif_omits_baseline_state_and_null_columns_without_them(tmp_path: Path):
    doc = build_sarif(
        new=[_issue(tmp_path, column=None, end_line=None, end_column=None)],
        known=[],
        ignored=[],
        tool_version="0",
        root=tmp_path,
        baseline_used=False,
    )
    result = doc["runs"][0]["results"][0]
    assert "baselineState" not in result
    assert result["locations"][0]["physicalLocation"]["region"] == {"startLine": 3}
