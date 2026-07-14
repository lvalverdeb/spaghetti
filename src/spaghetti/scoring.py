"""Health scoring and remediation planning."""
from __future__ import annotations

from collections import defaultdict

from spaghetti.models import Issue, RemediationStep, ScanResult, _display_path

__all__ = [
    "compute_score",
    "compute_priority_score",
    "build_remediation_plan",
    "plan_report",
]

# ── Health Scorecard ──────────────────────────────────────────────────────────

_SEVERITY_WEIGHT = {"error": 6.0, "warning": 1.5, "info": 0.3}
_GRADE_BANDS = [(90.0, "A"), (75.0, "B"), (60.0, "C"), (40.0, "D"), (0.0, "F")]


def compute_score(result: ScanResult) -> tuple[float, str]:
    """A rough 0-100 health score, weighted by severity and normalized per
    1,000 lines so larger packages aren't unfairly penalized."""
    if result.total_lines == 0:
        return 100.0, "A"
    penalty = sum(_SEVERITY_WEIGHT[i.severity] for i in result.issues)
    penalty_per_kloc = penalty / (result.total_lines / 1000)
    score = max(0.0, 100.0 - penalty_per_kloc)
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
    "dead-code": 0.5,
    "magic-number": 0.5,
    "missing-else": 0.5,
    "lazy-class": 1.5,
    "deep-inheritance": 3.0,
    "message-chain": 1.0,
    "excessive-decorators": 1.0,
    "pass-through-method": 1.0,
    "orphan-interface": 1.5,
}

_PRIORITY_LEVELS = [
    (12.0, "P0", "CRITICAL — fix immediately"),
    (7.0, "P1", "HIGH — fix this sprint"),
    (3.0, "P2", "MEDIUM — plan for next cycle"),
    (0.0, "P3", "LOW — track in backlog"),
]


def compute_priority_score(issue: Issue) -> float:
    """Priority score = severity_weight x fix_effort."""
    return _SEVERITY_WEIGHT[issue.severity] * _FIX_EFFORT.get(issue.rule, 1.0)


def _effort_label(effort: float) -> str:
    if effort >= 4.0:
        return "major"
    if effort >= 2.5:
        return "moderate"
    if effort >= 1.0:
        return "minor"
    return "trivial"


def _priority_label(score: float) -> tuple[str, str]:
    for threshold, level, desc in _PRIORITY_LEVELS:
        if score >= threshold:
            return level, desc
    return "P3", "LOW — track in backlog"


def build_remediation_plan(issues: list[Issue]) -> list[RemediationStep]:
    """Group issues by rule, compute priority scores, and return an ordered
    list of RemediationSteps."""
    by_rule: dict[str, list[Issue]] = defaultdict(list)
    for issue in issues:
        by_rule[issue.rule].append(issue)

    steps: list[RemediationStep] = []
    for rule, rule_issues in by_rule.items():
        effort = _FIX_EFFORT.get(rule, 1.0)
        max_sev = max(rule_issues, key=lambda i: _SEVERITY_WEIGHT[i.severity])
        score = _SEVERITY_WEIGHT[max_sev.severity] * effort
        priority, _ = _priority_label(score)

        files = sorted({_display_path(i.file) for i in rule_issues})

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


def plan_report(issues: list[Issue], *, top: int = 20) -> str:
    """Render a prioritized remediation plan as a text report."""
    steps = build_remediation_plan(issues)
    lines: list[str] = []

    lines.append("=" * 72)
    lines.append("  REMEDIATION PLAN — Prioritized Fix Order")
    lines.append("=" * 72)
    lines.append("")

    if not steps:
        lines.append("  All clean — nothing to fix.")
        return "\n".join(lines)

    by_priority: dict[str, int] = defaultdict(int)
    for step in steps:
        by_priority[step.priority] += 1

    lines.append("  Priority breakdown:")
    for _, label, desc in _PRIORITY_LEVELS:
        count = by_priority.get(label, 0)
        if count:
            lines.append(f"    {label}: {count} rule(s) — {desc}")
    lines.append("")

    lines.append(
        f"  {'#':<3} {'Pri':<4} {'Rule':<30} {'Sev':<4} {'Effort':<9} "
        f"{'Issues':>6}  {'Score':>5}"
    )
    lines.append(
        f"  {'─' * 3} {'─' * 4} {'─' * 30} {'─' * 4} {'─' * 9} "
        f"{'─' * 6}  {'─' * 5}"
    )

    shown = steps[:top]
    for idx, step in enumerate(shown, 1):
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

    lines.append("")
    lines.append("=" * 72)
    lines.append("  RECOMMENDED FIX ORDER")
    lines.append("=" * 72)
    lines.append("")

    for _, label, desc in _PRIORITY_LEVELS:
        level_steps = [s for s in steps if s.priority == label]
        if not level_steps:
            continue
        lines.append(f"  {label} — {desc}")
        for step in level_steps:
            lines.append(
                f"    • {step.rule} ({step.count} issue{'s' if step.count != 1 else ''}, "
                f"{step.effort} effort)"
            )
        lines.append("")

    return "\n".join(lines)
