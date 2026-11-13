"""Self-contained HTML report: scorecard, trend sparklines from the snapshot
store, hotspot ranking, per-file findings. One file, inline CSS, no script,
no server, no account — open it or attach it to a PR."""

from __future__ import annotations

from collections import defaultdict
from html import escape
from pathlib import Path

from spaghetti.churn import Hotspot
from spaghetti.config import LINES_PER_KLOC
from spaghetti.models import Issue, ScanResult, _display_path
from spaghetti.scoring import compute_score, file_penalties
from spaghetti.snapshots import Comparison, Snapshot

__all__ = ["build_html"]

_CSS = """
:root{--bg:#fff;--fg:#1b1f23;--muted:#6a737d;--line:#e1e4e8;--err:#c62828;--warn:#b26a00;--info:#2b6cb0;
--A:#1f8a3b;--B:#5a9e2f;--C:#c99a00;--D:#d9661f;--F:#c62828}
@media (prefers-color-scheme:dark){:root{--bg:#0d1117;--fg:#e6edf3;--muted:#8b949e;--line:#30363d}}
body{margin:0 auto;max-width:1100px;padding:24px;font:14px/1.5 -apple-system,BlinkMacSystemFont,Segoe UI,Helvetica,Arial,sans-serif;background:var(--bg);color:var(--fg)}
h1{font-size:22px;margin:0 0 4px}h2{font-size:17px;margin:32px 0 8px;border-bottom:1px solid var(--line);padding-bottom:4px}
.meta{color:var(--muted)}table{border-collapse:collapse;width:100%}th,td{text-align:left;padding:4px 8px;border-bottom:1px solid var(--line);vertical-align:top}
th{color:var(--muted);font-weight:600}td.num,th.num{text-align:right;font-variant-numeric:tabular-nums}
.grade{display:inline-block;min-width:1.6em;text-align:center;border-radius:4px;color:#fff;font-weight:700;padding:0 4px}
.sev-error{color:var(--err)}.sev-warning{color:var(--warn)}.sev-info{color:var(--info)}
code{font:12px/1.4 ui-monospace,SFMono-Regular,Menlo,monospace}details{margin:6px 0}summary{cursor:pointer}
.note{background:rgba(43,108,176,.08);border-left:3px solid var(--info);padding:6px 10px;margin:6px 0}
svg.spark{vertical-align:middle}.delta-up{color:var(--A)}.delta-down{color:var(--err)}
"""


def _grade_badge(grade: str) -> str:
    return f'<span class="grade" style="background:var(--{escape(grade)})">{escape(grade)}</span>'


def _sparkline(values: list[float], *, width: int = 140, height: int = 28) -> str:
    """Polyline of 0-100 scores; a single value draws a dot."""
    if not values:
        return ""
    n = len(values)
    pts = []
    for i, v in enumerate(values):
        x = 2 + (width - 4) * (i / (n - 1) if n > 1 else 0.5)
        y = 2 + (height - 4) * (1 - max(0.0, min(100.0, v)) / 100)
        pts.append(f"{x:.1f},{y:.1f}")
    body = (
        f'<polyline fill="none" stroke="currentColor" stroke-width="1.5" points="{" ".join(pts)}"/>'
        if n > 1
        else f'<circle cx="{pts[0].split(",")[0]}" cy="{pts[0].split(",")[1]}" r="2" fill="currentColor"/>'
    )
    return (
        f'<svg class="spark" width="{width}" height="{height}" viewBox="0 0 {width} {height}" '
        f'role="img" aria-label="{n} snapshot(s)">{body}</svg>'
    )


def _scorecard(
    packages: list[str],
    per_package: dict[str, ScanResult],
    total: ScanResult,
    history: list[Snapshot],
) -> str:
    rows = []
    entries = [(name, per_package[name]) for name in packages] + [("OVERALL", total)]
    for name, result in entries:
        score, grade = compute_score(result)
        key = "*" if name == "OVERALL" else name
        series = [s.scores[key] for s in history if key in s.scores] + [round(score, 1)]
        mi = result.maintainability_index
        rows.append(
            "<tr>"
            f"<td><code>{escape(name)}</code></td><td>{_grade_badge(grade)}</td>"
            f'<td class="num">{score:.1f}</td><td class="num">{result.files_scanned}</td>'
            f'<td class="num">{result.total_lines / LINES_PER_KLOC:.1f}</td>'
            f'<td class="num">{len(result.issues)}</td>'
            f'<td class="num sev-error">{result.error_count}</td>'
            f'<td class="num sev-warning">{result.warning_count}</td>'
            f'<td class="num sev-info">{result.info_count}</td>'
            f'<td class="num">{"—" if mi is None else f"{mi:.1f}"}</td>'
            f"<td>{_sparkline(series) if history else ''}</td></tr>"
        )
    return (
        "<table><thead><tr><th>Package</th><th>Grade</th><th class=num>Score</th><th class=num>Files</th>"
        "<th class=num>KLOC</th><th class=num>Findings</th><th class=num>Err</th><th class=num>Warn</th>"
        "<th class=num>Info</th><th class=num>MI</th><th>Trend</th></tr></thead><tbody>"
        + "".join(rows)
        + "</tbody></table>"
    )


def _comparison(comp: Comparison) -> str:
    cls = "delta-up" if comp.score_delta >= 0 else "delta-down"
    sign = "+" if comp.score_delta >= 0 else ""
    parts = [
        f'<p class="note">Compared to <code>{escape(comp.against.label)}</code>: score '
        f'{comp.score_before:.1f} → {comp.score_after:.1f} (<span class="{cls}">{sign}{comp.score_delta}</span>), '
        f"{len(comp.introduced)} introduced, {len(comp.fixed)} fixed, {len(comp.files_worse)} file(s) worse.</p>"
    ]
    if comp.introduced:
        parts.append("<h3>Introduced</h3><ul>")
        parts += [
            f'<li><span class="sev-{escape(i.severity)}">{escape(i.severity)}</span> '
            f"<code>{escape(_display_path(i.file))}:{i.line}</code> <code>{escape(i.rule)}</code> — "
            f"{escape(i.message)}</li>"
            for i in comp.introduced
        ]
        parts.append("</ul>")
    if comp.fixed:
        parts.append("<h3>Fixed</h3><ul>")
        parts += [
            f"<li><code>{escape(f.file)}</code> <code>{escape(f.rule)}</code> — {escape(f.message)}</li>"
            for f in comp.fixed
        ]
        parts.append("</ul>")
    return "".join(parts)


def _hotspots(spots: list[Hotspot], months: int, top: int) -> str:
    if not spots:
        return "<p class=meta>No hotspots: no file with findings was touched in the window.</p>"
    rows = "".join(
        f"<tr><td><code>{escape(h.file)}</code></td><td class=num>{h.penalty:.1f}</td>"
        f"<td class=num>{h.commits}</td><td class=num>{h.authors}</td><td class=num>{h.score:.1f}</td></tr>"
        for h in spots[:top]
    )
    return (
        f"<p class=meta>Health penalty × change frequency over the last {months} months. "
        "Files here are both tangled and actively edited — fix these first.</p>"
        "<table><thead><tr><th>File</th><th class=num>Penalty</th><th class=num>Commits</th>"
        f"<th class=num>Authors</th><th class=num>Hotspot</th></tr></thead><tbody>{rows}</tbody></table>"
    )


def _findings(issues: list[Issue]) -> str:
    by_file: dict[Path, list[Issue]] = defaultdict(list)
    for issue in issues:
        by_file[issue.file].append(issue)
    penalties = file_penalties(issues)
    blocks = []
    for path in sorted(by_file, key=lambda p: (-penalties[p], _display_path(p))):
        rows = "".join(
            f'<tr><td class=num>{i.line}</td><td class="sev-{escape(i.severity)}">{escape(i.severity)}</td>'
            f"<td><code>{escape(i.rule)}</code></td><td>{escape(i.message)}</td></tr>"
            for i in sorted(by_file[path], key=lambda i: (i.line, i.rule))
        )
        blocks.append(
            f"<details><summary><code>{escape(_display_path(path))}</code> — {len(by_file[path])} "
            f"finding(s), penalty {penalties[path]:.1f}</summary><table><thead><tr><th class=num>Line</th>"
            f"<th>Severity</th><th>Rule</th><th>Message</th></tr></thead><tbody>{rows}</tbody></table></details>"
        )
    return "".join(blocks) if blocks else "<p>No findings.</p>"


def _rule_summary(issues: list[Issue]) -> str:
    counts: dict[str, int] = defaultdict(int)
    for i in issues:
        counts[i.rule] += 1
    rows = "".join(
        f"<tr><td><code>{escape(rule)}</code></td><td class=num>{n}</td></tr>"
        for rule, n in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    )
    return f"<table><thead><tr><th>Rule</th><th class=num>Findings</th></tr></thead><tbody>{rows}</tbody></table>"


def build_html(
    *,
    packages: list[str],
    per_package: dict[str, ScanResult],
    total: ScanResult,
    issues: list[Issue],
    tool_version: str,
    sha: str | None,
    generated_at: str,
    notes: list[str],
    history: list[Snapshot],
    comparison: Comparison | None,
    hotspots: list[Hotspot] | None,
    hotspot_months: int,
    top: int,
) -> str:
    score, grade = compute_score(total)
    sections = [
        f"<h1>spaghetti report {_grade_badge(grade)} {score:.1f}</h1>",
        f'<p class="meta">spaghetti {escape(tool_version)} · {escape(generated_at)}'
        + (f" · <code>{escape(sha[:12])}</code>" if sha else "")
        + "</p>",
        "".join(f'<p class="note">{escape(n)}</p>' for n in notes),
        "<h2>Scorecard</h2>",
        _scorecard(packages, per_package, total, history),
    ]
    if comparison is not None:
        sections += ["<h2>Change</h2>", _comparison(comparison)]
    if hotspots is not None:
        sections += ["<h2>Hotspots</h2>", _hotspots(hotspots, hotspot_months, top)]
    sections += [
        f"<h2>Findings ({len(issues)})</h2>",
        _findings(issues),
        "<h2>By rule</h2>",
        _rule_summary(issues),
    ]
    return (
        '<!DOCTYPE html>\n<html lang="en"><head><meta charset="utf-8">'
        f"<title>spaghetti report — {escape(grade)} {score:.1f}</title>"
        f'<meta name="viewport" content="width=device-width,initial-scale=1"><style>{_CSS}</style></head>'
        f"<body>{''.join(sections)}</body></html>\n"
    )
