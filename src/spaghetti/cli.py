"""CLI entry point for the spaghetti detector."""

from __future__ import annotations

import argparse
import asyncio
import sys
from collections import defaultdict
from pathlib import Path

import yaml

from spaghetti.models import Issue, ScanResult, _display_path
from spaghetti.scoring import compute_score, plan_report

__all__ = ["main", "resolve_packages"]


def _load_packages_from_config(config_path: Path) -> dict[str, Path]:
    """Load a ``{name: path}`` package registry from a YAML config file."""
    try:
        raw = yaml.safe_load(config_path.read_text())
    except OSError as exc:
        raise SystemExit(f"error: could not read --config {config_path}: {exc}") from exc
    except yaml.YAMLError as exc:
        raise SystemExit(f"error: could not parse --config {config_path}: {exc}") from exc

    if not isinstance(raw, dict) or not isinstance(raw.get("packages"), dict):
        raise SystemExit(
            f"error: {config_path} must define a top-level 'packages' mapping "
            f"of {{name: path}}, e.g.:\n\npackages:\n  my-pkg: my-pkg/src/my_pkg\n"
        )

    base_dir = config_path.resolve().parent
    return {
        str(name): (base_dir / str(rel_path)).resolve()
        for name, rel_path in raw["packages"].items()
    }


def _parse_package_args(entries: list[str], *, cwd: Path) -> dict[str, Path]:
    """Parse repeated ``--package NAME=PATH`` CLI entries into a registry."""
    resolved: dict[str, Path] = {}
    for entry in entries:
        name, sep, raw_path = entry.partition("=")
        name, raw_path = name.strip(), raw_path.strip()
        if not sep or not name or not raw_path:
            raise SystemExit(f"error: --package expects NAME=PATH, got {entry!r}")
        resolved[name] = (cwd / raw_path).resolve()
    return resolved


def resolve_packages(
    *,
    config_path: Path | None,
    package_args: list[str],
    defaults: dict[str, Path],
    cwd: Path,
) -> dict[str, Path]:
    """Build the effective ``{name: path}`` package registry for one run."""
    if config_path is None and not package_args:
        return dict(defaults)

    packages = _load_packages_from_config(config_path) if config_path is not None else {}
    packages.update(_parse_package_args(package_args, cwd=cwd))
    return packages


def main() -> int:
    from spaghetti.config import (
        DEFAULT_MIN_DUPLICATE_LINES,
        DEFAULT_PACKAGES,
        DEFAULT_TWIN_SIMILARITY,
    )

    parser = argparse.ArgumentParser(description="Spaghetti Code Detector")
    parser.add_argument(
        "--config",
        type=Path,
        default=None,
        help=(
            "YAML file with a 'packages: {name: path}' mapping, replacing the "
            "built-in default package list. Paths resolve relative to the "
            "config file."
        ),
    )
    parser.add_argument(
        "--package",
        dest="package_args",
        action="append",
        default=[],
        metavar="NAME=PATH",
        help=(
            "Add or override one package as NAME=PATH (repeatable). Applied on "
            "top of --config or the built-in defaults; PATH resolves relative "
            "to the current directory."
        ),
    )
    parser.add_argument(
        "--packages",
        nargs="*",
        default=None,
        help=(
            "Names to scan from the resolved registry (default: all — see "
            "--config/--package for how the registry is built)"
        ),
    )
    parser.add_argument(
        "--severity",
        choices=["info", "warning", "error"],
        default="info",
        help="Minimum severity to display (default: info)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output as JSON",
    )
    parser.add_argument(
        "--top",
        type=int,
        default=5,
        help="Number of worst files to list (default: 5)",
    )
    parser.add_argument(
        "--exclude",
        nargs="*",
        default=[],
        help="Path substrings to exclude from scanning (default: none)",
    )
    parser.add_argument(
        "--min-duplicate-lines",
        type=int,
        default=DEFAULT_MIN_DUPLICATE_LINES,
        help=f"Minimum function length to consider for duplicate-body detection (default: {DEFAULT_MIN_DUPLICATE_LINES})",
    )
    parser.add_argument(
        "--twin-similarity",
        type=float,
        default=DEFAULT_TWIN_SIMILARITY,
        help=f"Minimum text-similarity ratio (0-1) to flag a sync/async twin pair (default: {DEFAULT_TWIN_SIMILARITY})",
    )
    parser.add_argument(
        "--plan",
        action="store_true",
        help="Output a prioritized remediation plan instead of the standard report",
    )
    args = parser.parse_args()

    # Late import to avoid circular dependency (cli → scanner → detector → cli)
    # Set PACKAGES on detector module (the canonical place tests monkeypatch)
    import spaghetti.detector as _det
    from spaghetti.scanner import review_packages_concurrently

    _det.PACKAGES = resolve_packages(
        config_path=args.config,
        package_args=args.package_args,
        defaults=DEFAULT_PACKAGES,
        cwd=Path.cwd(),
    )
    if not _det.PACKAGES:
        parser.error("no packages to scan — the resolved package registry is empty")

    args.packages = args.packages if args.packages is not None else list(_det.PACKAGES.keys())
    unknown = [p for p in args.packages if p not in _det.PACKAGES]
    if unknown:
        parser.error(
            f"unknown package(s): {', '.join(unknown)}. Available: {', '.join(sorted(_det.PACKAGES))}"
        )

    # A resolved-but-nonexistent path (e.g. a --package NAME=PATH typo, or a
    # relative PATH resolved against the wrong cwd) must not be confused
    # with "scanned and found nothing wrong": scan_package() silently
    # returns an empty ScanResult for a missing path, which without this
    # check reports a perfect A/100.0 grade — indistinguishable from a
    # genuinely clean package — for a package that was never actually
    # scanned at all.
    missing = [p for p in args.packages if not _det.PACKAGES[p].exists()]
    if missing:
        parser.error(
            "package path(s) do not exist: " + ", ".join(f"{p}={_det.PACKAGES[p]}" for p in missing)
        )

    severity_order = {"info": 0, "warning": 1, "error": 2}
    min_severity = severity_order[args.severity]

    per_package: dict[str, ScanResult] = asyncio.run(
        review_packages_concurrently(
            args.packages,
            exclude=args.exclude,
            min_duplicate_lines=args.min_duplicate_lines,
            twin_similarity=args.twin_similarity,
        )
    )

    total_result = ScanResult()
    for pkg_name in args.packages:
        result = per_package[pkg_name]
        total_result.issues.extend(result.issues)
        total_result.files_scanned += result.files_scanned
        total_result.functions_scanned += result.functions_scanned
        total_result.total_lines += result.total_lines
        total_result.suppressed += result.suppressed

    filtered = [i for i in total_result.issues if severity_order[i.severity] >= min_severity]

    if args.plan:
        print(plan_report(filtered, top=args.top))
    elif args.json:
        import json

        output = {
            "issues": [
                {
                    "file": _display_path(i.file),
                    "line": i.line,
                    "severity": i.severity,
                    "rule": i.rule,
                    "message": i.message,
                    "package": i.package,
                }
                for i in filtered
            ],
            "suppressed": total_result.suppressed,
        }
        print(json.dumps(output, indent=2))
    else:
        _render_text_report(filtered, total_result, per_package, args)

    if total_result.error_count > 0:
        return 2
    if total_result.warning_count > 0:
        return 1
    return 0


def _render_text_report(
    filtered: list[Issue],
    total_result: ScanResult,
    per_package: dict[str, ScanResult],
    args: argparse.Namespace,
) -> None:
    """Render the full text report to stdout."""
    by_package: dict[str, list[Issue]] = defaultdict(list)
    for issue in filtered:
        by_package[issue.package].append(issue)

    by_file: dict[str, list[Issue]] = defaultdict(list)
    for issue in filtered:
        key = _display_path(issue.file)
        by_file[key].append(issue)

    print("=" * 72)
    print("  SPAGHETTI CODE DETECTION REPORT")
    print("=" * 72)
    print()
    print(f"  Files scanned:     {total_result.files_scanned}")
    print(f"  Lines scanned:     {total_result.total_lines}")
    print(f"  Functions scanned: {total_result.functions_scanned}")
    print(f"  Total issues:      {len(filtered)}")
    print(f"    Errors:          {sum(1 for i in filtered if i.severity == 'error')}")
    print(f"    Warnings:        {sum(1 for i in filtered if i.severity == 'warning')}")
    print(f"    Info:            {sum(1 for i in filtered if i.severity == 'info')}")
    if total_result.suppressed:
        print(f"  Suppressed:        {total_result.suppressed} (inline spaghetti-ignore markers)")
    print()

    # ── PACKAGE HEALTH SCORECARD ─────────────────────────────────────────────
    print("=" * 72)
    print("  PACKAGE HEALTH SCORECARD")
    print("=" * 72)
    print()
    print(f"  {'Package':<16} {'Grade':>5} {'Score':>7}  {'Files':>6} {'KLOC':>6} {'Issues':>7}")
    print(f"  {'─' * 16} {'─' * 5} {'─' * 7}  {'─' * 6} {'─' * 6} {'─' * 7}")
    for pkg_name in args.packages:
        result = per_package[pkg_name]
        score, grade = compute_score(result)
        kloc = result.total_lines / 1000
        print(
            f"  {pkg_name:<16} {grade:>5} {score:>6.1f}  "
            f"{result.files_scanned:>6} {kloc:>6.1f} {len(result.issues):>7}"
        )
    overall_score, overall_grade = compute_score(total_result)
    print(f"  {'─' * 16} {'─' * 5} {'─' * 7}  {'─' * 6} {'─' * 6} {'─' * 7}")
    print(
        f"  {'OVERALL':<16} {overall_grade:>5} {overall_score:>6.1f}  "
        f"{total_result.files_scanned:>6} {total_result.total_lines / 1000:>6.1f} {len(total_result.issues):>7}"
    )
    print()

    # ── AFFECTED FILES ───────────────────────────────────────────────────────
    def _file_sort_key(item: tuple[str, list[Issue]]) -> tuple[int, int, str]:
        path, file_issues = item
        error_count = sum(1 for i in file_issues if i.severity == "error")
        return -error_count, -len(file_issues), path

    sorted_files = sorted(by_file.items(), key=_file_sort_key)

    print("=" * 72)
    print("  AFFECTED FILES")
    print("=" * 72)
    print()

    if not sorted_files:
        print("  All clean — no issues found.")
        print()
    else:
        print(f"  {'File':<58} {'E':>3} {'W':>3} {'I':>3}  Rules")
        print(f"  {'─' * 58} {'─' * 3} {'─' * 3} {'─' * 3}  {'─' * 28}")
        for fpath, issues in sorted_files:
            e = sum(1 for i in issues if i.severity == "error")
            w = sum(1 for i in issues if i.severity == "warning")
            inf = sum(1 for i in issues if i.severity == "info")
            rules = sorted(set(i.rule for i in issues))
            rules_str = ", ".join(rules)
            if len(rules_str) > 28:
                rules_str = rules_str[:25] + "..."
            marker = "✖" if e > 0 else "⚠" if w > 0 else "ℹ"
            print(f"  {marker} {fpath:<56} {e:>3} {w:>3} {inf:>3}  {rules_str}")
        print()

        print(f"  Worst files (top {args.top}):")
        for rank, (fpath, issues) in enumerate(sorted_files[: args.top], 1):
            e = sum(1 for i in issues if i.severity == "error")
            w = sum(1 for i in issues if i.severity == "warning")
            inf = sum(1 for i in issues if i.severity == "info")
            print(f"    {rank}. {fpath} ({len(issues)} issues: {e}E {w}W {inf}I)")
        print()

    # ── CROSS-FILE FINDINGS ──────────────────────────────────────────────────
    cross_file_rules = {"duplicate-function-body", "sync-async-duplication", "import-cycle"}
    cross_file_issues = [i for i in filtered if i.rule in cross_file_rules]
    if cross_file_issues:
        print("=" * 72)
        print("  CROSS-FILE FINDINGS (duplication & import cycles)")
        print("=" * 72)
        print()
        for issue in cross_file_issues:
            print(str(issue))
        print()

    # ── DETAILED FINDINGS ────────────────────────────────────────────────────
    print("=" * 72)
    print("  DETAILED FINDINGS")
    print("=" * 72)
    print()

    for pkg_name in args.packages:
        pkg_issues = [i for i in by_package.get(pkg_name, [])]
        if not pkg_issues:
            print(f"  ✅ {pkg_name}: clean")
            print()
            continue

        errors = sum(1 for i in pkg_issues if i.severity == "error")
        warnings = sum(1 for i in pkg_issues if i.severity == "warning")
        infos = sum(1 for i in pkg_issues if i.severity == "info")

        status = "✖" if errors > 0 else "⚠" if warnings > 0 else "✓"
        print(f"  {status} {pkg_name}: {len(pkg_issues)} issues ({errors}E {warnings}W {infos}I)")
        print("-" * 72)

        pkg_files: dict[str, list[Issue]] = defaultdict(list)
        for issue in pkg_issues:
            key = _display_path(issue.file)
            pkg_files[key].append(issue)

        sorted_pkg_files = sorted(pkg_files.items(), key=_file_sort_key)

        for fpath, file_issues in sorted_pkg_files:
            fe = sum(1 for i in file_issues if i.severity == "error")
            fw = sum(1 for i in file_issues if i.severity == "warning")
            fi = sum(1 for i in file_issues if i.severity == "info")
            status = "✖" if fe > 0 else "⚠" if fw > 0 else "ℹ"
            print(f"    {status} {fpath} ({len(file_issues)} issues: {fe}E {fw}W {fi}I)")

            for issue in file_issues:
                icon = {"error": "✖", "warning": "⚠", "info": "ℹ"}[issue.severity]
                print(f"      {icon} L{issue.line:<5} [{issue.rule}] {issue.message}")
        print()

    # ── RULE SUMMARY ─────────────────────────────────────────────────────────
    rule_counts: dict[str, int] = defaultdict(int)
    rule_by_severity: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for issue in filtered:
        rule_counts[issue.rule] += 1
        rule_by_severity[issue.rule][issue.severity] += 1

    print("=" * 72)
    print("  RULE SUMMARY")
    print("=" * 72)
    print(f"  {'Rule':<30} {'Total':>5} {'E':>3} {'W':>3} {'I':>3}")
    print(f"  {'─' * 30} {'─' * 5} {'─' * 3} {'─' * 3} {'─' * 3}")
    for rule_name, count in sorted(rule_counts.items(), key=lambda x: -x[1]):
        rs = rule_by_severity[rule_name]
        print(f"  {rule_name:<30} {count:>5} {rs['error']:>3} {rs['warning']:>3} {rs['info']:>3}")
    print()


if __name__ == "__main__":
    sys.exit(main())
