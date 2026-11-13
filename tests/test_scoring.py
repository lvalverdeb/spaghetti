"""Tests for spaghetti.scoring: compute_score, remediation priority, plan_report."""

from __future__ import annotations

from pathlib import Path

from spaghetti.models import Issue, ScanResult
from spaghetti.scoring import (
    build_remediation_plan,
    compute_priority_score,
    compute_score,
    plan_report,
)

# ── compute_score() ──────────────────────────────────────────────────────────


def test_compute_score_perfect_for_empty_result():
    score, grade = compute_score(ScanResult())
    assert score == 100.0
    assert grade == "A"


def test_compute_score_degrades_with_errors():
    result = ScanResult(
        issues=[
            Issue(file=Path("f.py"), line=1, severity="error", rule="x", message="m")
            for _ in range(5)
        ],
        total_lines=1000,
    )
    score, grade = compute_score(result)
    assert score < 100.0
    assert grade != "A"


# ── Remediation Priority ────────────────────────────────────────────────────


def test_compute_priority_score_error_high_effort():
    issue = Issue(
        file=Path("f.py"),
        line=1,
        severity="error",
        rule="import-cycle",
        message="circular import",
        package="pkg",
    )
    score = compute_priority_score(issue)
    # error=6.0 × effort=5.0 = 30.0
    assert score == 30.0


def test_compute_priority_score_warning_moderate_effort():
    issue = Issue(
        file=Path("f.py"),
        line=1,
        severity="warning",
        rule="long-function",
        message="too long",
        package="pkg",
    )
    score = compute_priority_score(issue)
    # warning=1.5 × effort=2.5 = 3.75
    assert score == 3.75


def test_compute_priority_score_info_trivial():
    issue = Issue(
        file=Path("f.py"),
        line=1,
        severity="info",
        rule="dead-code",
        message="unreachable",
        package="pkg",
    )
    score = compute_priority_score(issue)
    # info=0.3 × effort=0.5 = 0.15
    assert score == 0.15


def test_compute_priority_score_high_coupling_major_effort():
    # Regression test: high-coupling was missing from _FIX_EFFORT entirely,
    # silently falling back to the 1.0 (trivial) default — understating the
    # real cost of splitting an overloaded hub module.
    issue = Issue(
        file=Path("f.py"),
        line=1,
        severity="warning",
        rule="high-coupling",
        message="overloaded hub",
        package="pkg",
    )
    score = compute_priority_score(issue)
    # warning=1.5 × effort=4.0 = 6.0
    assert score == 6.0


def test_build_remediation_plan_groups_by_rule():
    issues = [
        Issue(Path("a.py"), 1, "error", "import-cycle", "cycle", "pkg"),
        Issue(Path("b.py"), 2, "error", "import-cycle", "cycle", "pkg"),
        Issue(Path("c.py"), 3, "warning", "long-function", "long", "pkg"),
    ]
    steps = build_remediation_plan(issues)
    assert len(steps) == 2
    # import-cycle has higher score (30.0) than long-function (3.75)
    assert steps[0].rule == "import-cycle"
    assert steps[0].count == 2
    assert steps[1].rule == "long-function"
    assert steps[1].count == 1


def test_build_remediation_plan_priority_labels():
    issues = [
        Issue(Path("a.py"), 1, "error", "god-class", "too big", "pkg"),
        Issue(Path("b.py"), 2, "info", "todo-marker", "todo", "pkg"),
    ]
    steps = build_remediation_plan(issues)
    assert steps[0].priority == "P0"  # error × effort=5.0 = 30.0
    assert steps[1].priority == "P3"  # info × effort=0.5 = 0.15


def test_plan_report_contains_all_sections():
    issues = [
        Issue(Path("a.py"), 1, "error", "import-cycle", "cycle", "pkg"),
        Issue(Path("b.py"), 2, "warning", "long-function", "long", "pkg"),
    ]
    report = plan_report(issues)
    assert "REMEDIATION PLAN" in report
    assert "RECOMMENDED FIX ORDER" in report
    assert "import-cycle" in report
    assert "long-function" in report
    assert "P0" in report or "P1" in report
    assert "P2" in report or "P3" in report


def test_plan_report_empty_issues():
    report = plan_report([])
    assert "All clean" in report


def test_plan_report_groups_files():
    issues = [
        Issue(Path("a.py"), 1, "warning", "long-function", "long", "pkg"),
        Issue(Path("a.py"), 2, "warning", "long-function", "long", "pkg"),
        Issue(Path("b.py"), 3, "warning", "long-function", "long", "pkg"),
    ]
    steps = build_remediation_plan(issues)
    assert len(steps) == 1
    assert steps[0].count == 3
    assert len(steps[0].files) == 2  # a.py and b.py


def test_plan_report_truncates_by_top():
    issues = [Issue(Path(f"file{i}.py"), 1, "info", f"rule-{i}", "msg", "pkg") for i in range(30)]
    report = plan_report(issues, top=5)
    assert "and 25 more rules" in report
