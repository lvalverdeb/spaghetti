"""Health scoring and remediation planning."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable
from pathlib import Path

from spaghetti.config import BANNER_WIDTH, DEFAULT_PLAN_TOP, LINES_PER_KLOC
from spaghetti.models import Issue, RemediationStep, ScanResult, _display_path

__all__ = [
    "compute_score",
    "compute_priority_score",
    "build_remediation_plan",
    "file_penalties",
    "plan_report",
]

# ── Health Scorecard ──────────────────────────────────────────────────────────

_SEVERITY_WEIGHT = {"error": 6.0, "warning": 1.5, "info": 0.3}
_GRADE_BANDS = [(90.0, "A"), (75.0, "B"), (60.0, "C"), (40.0, "D"), (0.0, "F")]
_MAX_SCORE = 100.0


def file_penalties(issues: list[Issue]) -> dict[Path, float]:
    """Per-file health cost: the sum of severity weights of its findings — the
    same quantity ``compute_score`` normalises per KLOC, left raw so files can
    be ranked and tracked across snapshots."""
    penalties: dict[Path, float] = defaultdict(float)
    for issue in issues:
        penalties[issue.file] += _SEVERITY_WEIGHT[issue.severity]
    return dict(penalties)


def compute_score(result: ScanResult) -> tuple[float, str]:
    """A rough 0-100 health score, weighted by severity and normalized per
    1,000 lines so larger packages aren't unfairly penalized."""
    if result.total_lines == 0:
        return _MAX_SCORE, "A"
    penalty = sum(_SEVERITY_WEIGHT[i.severity] for i in result.issues)
    penalty_per_kloc = penalty / (result.total_lines / LINES_PER_KLOC)
    score = max(0.0, _MAX_SCORE - penalty_per_kloc)
    grade = next(g for threshold, g in _GRADE_BANDS if score >= threshold)
    return score, grade


# ── Remediation Priority ──────────────────────────────────────────────────────

_FIX_EFFORT: dict[str, float] = {
    "import-cycle": 5.0,
    "layer-violation": 4.0,
    "transport-in-library": 4.0,
    "god-class": 5.0,
    "god-module": 4.0,
    "sync-async-duplication": 3.0,
    "duplicate-function-body": 3.0,
    "long-function": 2.5,
    "high-complexity": 3.0,
    "high-cognitive-complexity": 3.0,
    "duplicate-code": 3.0,
    "low-maintainability": 4.0,
    "deep-nesting": 2.0,
    "too-many-params": 2.0,
    "boolean-flag-params": 1.5,
    "excessive-returns": 1.5,
    "missing-return-type": 1.0,
    "missing-param-type": 0.5,
    "untyped-dict": 0.5,
    "unused-import": 0.5,
    "star-import": 0.5,
    "potential-circular-import": 2.5,
    "swallowed-exception": 1.5,
    "bare-except": 1.0,
    "mutable-default": 0.5,
    "scope-mutation": 2.0,
    "global-mutable": 1.5,
    "encapsulation-violation": 1.0,
    "long-file": 2.5,
    "todo-marker": 0.5,
    "syntax-error": 1.0,
    "unreadable-file": 1.0,
    "dead-code": 0.5,
    "magic-number": 0.5,
    "magic-string": 0.5,
    "missing-else": 0.5,
    "lazy-class": 1.5,
    "deep-inheritance": 3.0,
    "message-chain": 1.0,
    "excessive-decorators": 1.0,
    "pass-through-method": 1.0,
    "orphan-interface": 1.5,
    "high-coupling": 4.0,
}

_PRIORITY_LEVELS = [
    (12.0, "P0", "CRITICAL — fix immediately"),
    (7.0, "P1", "HIGH — fix this sprint"),
    (3.0, "P2", "MEDIUM — plan for next cycle"),
    (0.0, "P3", "LOW — track in backlog"),
]

# Module-level (not inside plan_report) so the column widths are computed
# once rather than re-literalized every render.
_PLAN_TABLE_DIVIDER = f"  {'─' * 3} {'─' * 4} {'─' * 30} {'─' * 4} {'─' * 9} {'─' * 6}  {'─' * 5}"


def compute_priority_score(issue: Issue) -> float:
    """Priority score = severity_weight x fix_effort."""
    return _SEVERITY_WEIGHT[issue.severity] * _FIX_EFFORT.get(issue.rule, 1.0)


_EFFORT_LEVELS = [
    (4.0, "major"),
    (2.5, "moderate"),
    (1.0, "minor"),
]


def _effort_label(effort: float) -> str:
    for threshold, label in _EFFORT_LEVELS:
        if effort >= threshold:
            return label
    return "trivial"


def _priority_label(score: float) -> tuple[str, str]:
    for threshold, level, desc in _PRIORITY_LEVELS:
        if score >= threshold:
            return level, desc
    return "P3", "LOW — track in backlog"


def build_remediation_plan(
    issues: list[Issue], *, weight: Callable[[Path], float] | None = None
) -> list[RemediationStep]:
    """Group issues by rule, compute priority scores, and return an ordered
    list of RemediationSteps. ``weight`` (default 1) multiplies a finding's
    score by its file's churn factor so P0 is "worst *and* hottest", not just
    worst (see ``spaghetti.churn``); files inside a step are listed hottest
    first."""
    by_rule: dict[str, list[Issue]] = defaultdict(list)
    for issue in issues:
        by_rule[issue.rule].append(issue)
    weight_of = weight or (lambda _path: 1.0)

    steps: list[RemediationStep] = []
    for rule, rule_issues in by_rule.items():
        effort = _FIX_EFFORT.get(rule, 1.0)
        max_sev = max(rule_issues, key=lambda i: _SEVERITY_WEIGHT[i.severity])
        score = max(_SEVERITY_WEIGHT[i.severity] * effort * weight_of(i.file) for i in rule_issues)
        priority, _ = _priority_label(score)

        hottest: dict[str, float] = {}
        for i in rule_issues:
            path = _display_path(i.file)
            hottest[path] = max(hottest.get(path, 0.0), weight_of(i.file))
        files = sorted(hottest, key=lambda p: (-hottest[p], p))

        steps.append(
            RemediationStep(
                priority=priority,
                rule=rule,
                severity=max_sev.severity,
                effort=_effort_label(effort),
                files=files,
                count=len(rule_issues),
                description=rule_issues[0].message,
                score=score,
            )
        )

    steps.sort(key=lambda s: (-s.score, -s.count))
    return steps


def plan_report(
    issues: list[Issue],
    *,
    top: int = DEFAULT_PLAN_TOP,
    weight: Callable[[Path], float] | None = None,
    note: str | None = None,
) -> str:
    """Render a prioritized remediation plan as a text report."""
    steps = build_remediation_plan(issues, weight=weight)
    lines = ["=" * BANNER_WIDTH, "  REMEDIATION PLAN — Prioritized Fix Order", "=" * BANNER_WIDTH]
    if note:
        lines.append(f"  {note}")
    lines.append("")
    if not steps:
        lines.append("  All clean — nothing to fix.")
        return "\n".join(lines)
    lines += _plan_breakdown(steps)
    lines += _plan_table(steps, top)
    lines += ["", "=" * BANNER_WIDTH, "  RECOMMENDED FIX ORDER", "=" * BANNER_WIDTH, ""]
    lines += _plan_fix_order(steps)
    return "\n".join(lines)


def _plan_breakdown(steps: list[RemediationStep]) -> list[str]:
    by_priority: dict[str, int] = defaultdict(int)
    for step in steps:
        by_priority[step.priority] += 1
    lines = ["  Priority breakdown:"]
    for _, label, desc in _PRIORITY_LEVELS:
        if by_priority.get(label):
            lines.append(f"    {label}: {by_priority[label]} rule(s) — {desc}")
    lines.append("")
    return lines


def _plan_table(steps: list[RemediationStep], top: int) -> list[str]:
    lines = [
        f"  {'#':<3} {'Pri':<4} {'Rule':<30} {'Sev':<4} {'Effort':<9} {'Issues':>6}  {'Score':>5}",
        _PLAN_TABLE_DIVIDER,
    ]
    for idx, step in enumerate(steps[:top], 1):
        sev_icon = {"error": "✖", "warning": "⚠", "info": "ℹ"}[step.severity]
        file_preview = step.files[0] if step.files else "?"
        if len(step.files) > 1:
            file_preview += f" +{len(step.files) - 1}"
        lines.append(
            f"  {idx:<3} {step.priority:<4} {step.rule:<30} {sev_icon:<4} "
            f"{step.effort:<9} {step.count:>6}  {step.score:>5.1f}"
        )
        lines.append(f"      └─ {file_preview}")
    if len(steps) > top:
        lines.append(f"  ... and {len(steps) - top} more rules (use --plan to see all)")
    return lines


def _plan_fix_order(steps: list[RemediationStep]) -> list[str]:
    lines: list[str] = []
    for _, label, desc in _PRIORITY_LEVELS:
        level_steps = [s for s in steps if s.priority == label]
        if not level_steps:
            continue
        lines.append(f"  {label} — {desc}")
        for step in level_steps:
            plural = "s" if step.count != 1 else ""
            lines.append(f"    • {step.rule} ({step.count} issue{plural}, {step.effort} effort)")
        lines.append("")
    return lines
