"""CLI entry point for the spaghetti detector."""

from __future__ import annotations

import argparse
import asyncio
import sys
from collections import defaultdict
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

import yaml

from spaghetti.config import BANNER_WIDTH, LINES_PER_KLOC
from spaghetti.models import Issue, ScanConfig, ScanResult, _display_path
from spaghetti.scoring import compute_score, plan_report

__all__ = ["main", "resolve_packages", "discover_cwd_packages"]

_SEVERITY_ORDER = {"info": 0, "warning": 1, "error": 2}
_SEVERITY_ICON = {"error": "✖", "warning": "⚠", "info": "ℹ"}
_JSON_INDENT = 2

# Process exit codes: worst issue severity found, or 0 if the run was clean.
_EXIT_CLEAN = 0
_EXIT_WARNING = 1
_EXIT_ERROR = 2


def _spaghetti_version() -> str:
    """Installed ``spaghetti-detector`` version, read from package metadata
    (not hardcoded) so it can never drift from ``pyproject.toml``."""
    try:
        return version("spaghetti-detector")
    except PackageNotFoundError:
        return "unknown"


# Table dividers for the text report — module-level (not inside a function)
# so the column widths are computed once and can't drift from each other
# across the two places some of them are printed.
_SCORECARD_DIVIDER = f"  {'─' * 16} {'─' * 5} {'─' * 7}  {'─' * 6} {'─' * 6} {'─' * 7}"
_AFFECTED_FILES_DIVIDER = f"  {'─' * 58} {'─' * 3} {'─' * 3} {'─' * 3}  {'─' * 28}"
_RULE_SUMMARY_DIVIDER = f"  {'─' * 30} {'─' * 5} {'─' * 3} {'─' * 3} {'─' * 3}"

# The "Rules" column in AFFECTED FILES — same width the divider above uses
# for it, so a longer rules list truncates right at the column edge.
_RULES_COL_WIDTH = 28


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


_NOISE_DIR_NAMES = frozenset({"__pycache__", "node_modules", "build", "dist", "site-packages"})

DEFAULT_CWD_EXCLUDES: list[str] = [
    "/.venv/",
    "/venv/",
    "/.git/",
    "/__pycache__/",
    "/node_modules/",
    "/build/",
    "/dist/",
    ".egg-info",
    "/.mypy_cache/",
    "/.pytest_cache/",
    "/.ruff_cache/",
    "/.tox/",
    "/site-packages/",
]


def _is_noise_dir(name: str) -> bool:
    return name.startswith(".") or name in _NOISE_DIR_NAMES or name.endswith(".egg-info")


def discover_cwd_packages(cwd: Path) -> tuple[dict[str, Path], str | None]:
    """Auto-discover a ``{name: path}`` registry from *cwd* for a bare
    ``spaghetti`` invocation (no --config/--package given) — this is what
    keeps a no-args run from silently defaulting to the workspace's
    boti/boti-data/boti-dask registry: it scans whatever's actually under
    the current directory instead.

    Each immediate, non-noise subdirectory of *cwd* containing at least one
    ``.py`` file anywhere in its subtree becomes its own named package.
    ``.py`` files sitting directly in *cwd* (outside any subdirectory) are
    grouped into one additional package named after *cwd* itself — the
    second return value is that package's name (``None`` if there were no
    such loose files), so callers can scan it non-recursively and avoid
    double-scanning the subdirectories already registered on their own.
    """
    packages: dict[str, Path] = {}
    for entry in sorted(cwd.iterdir()):
        if not entry.is_dir() or _is_noise_dir(entry.name):
            continue
        if next(entry.rglob("*.py"), None) is not None:
            packages[entry.name] = entry

    loose_root_name: str | None = None
    if next(cwd.glob("*.py"), None) is not None:
        loose_root_name = cwd.resolve().name or str(cwd.resolve())
        if loose_root_name in packages:
            loose_root_name = f"{loose_root_name} (root)"
        packages[loose_root_name] = cwd

    return packages, loose_root_name


def _build_arg_parser() -> argparse.ArgumentParser:
    from spaghetti.config import (
        DEFAULT_MIN_DUPLICATE_LINES,
        DEFAULT_TOP_FILES,
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
        default=DEFAULT_TOP_FILES,
        help=f"Number of worst files to list (default: {DEFAULT_TOP_FILES})",
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
    return parser


def main() -> int:
    from spaghetti.config import DEFAULT_PACKAGES

    parser = _build_arg_parser()
    args = parser.parse_args()

    # Late import to avoid circular dependency (cli → scanner → detector → cli)
    # Set PACKAGES on detector module (the canonical place tests monkeypatch)
    import spaghetti.detector as _det
    from spaghetti.scanner import review_packages_concurrently

    # A bare invocation (no --config, no --package) must never silently fall
    # back to the workspace's built-in boti/boti-data/boti-dask registry —
    # instead it auto-discovers whatever's actually under the current
    # directory. --config/--package (in any combination) opt back into the
    # explicit registry-resolution path below, unchanged.
    run_exclude = args.exclude
    non_recursive: frozenset[str] = frozenset()
    if args.config is None and not args.package_args:
        _det.PACKAGES, loose_root_name = discover_cwd_packages(Path.cwd())
        run_exclude = args.exclude + DEFAULT_CWD_EXCLUDES
        if loose_root_name is not None:
            non_recursive = frozenset({loose_root_name})
    else:
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

    min_severity = _SEVERITY_ORDER[args.severity]

    per_package: dict[str, ScanResult] = asyncio.run(
        review_packages_concurrently(
            args.packages,
            config=ScanConfig(
                exclude=run_exclude,
                min_duplicate_lines=args.min_duplicate_lines,
                twin_similarity=args.twin_similarity,
            ),
            non_recursive=non_recursive,
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
        total_result.ignored.extend(result.ignored)

    filtered = [i for i in total_result.issues if _SEVERITY_ORDER[i.severity] >= min_severity]

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
            "ignored": [
                {
                    "file": _display_path(i.file),
                    "line": i.line,
                    "severity": i.severity,
                    "rule": i.rule,
                    "message": i.message,
                    "package": i.package,
                    "reason": i.reason,
                }
                for i in total_result.ignored
            ],
        }
        print(json.dumps(output, indent=_JSON_INDENT))
    else:
        _render_text_report(filtered, total_result, per_package, args)

    if total_result.error_count > 0:
        return _EXIT_ERROR
    if total_result.warning_count > 0:
        return _EXIT_WARNING
    return _EXIT_CLEAN


def _file_sort_key(item: tuple[str, list[Issue]]) -> tuple[int, int, str]:
    path, file_issues = item
    error_count, _warnings, _infos = _severity_counts(file_issues)
    return -error_count, -len(file_issues), path


def _severity_counts(issues: list[Issue]) -> tuple[int, int, int]:
    """(errors, warnings, infos) — the report renders this same tally at
    several different groupings (overall, per-file, per-package)."""
    errors = sum(1 for i in issues if i.severity == "error")
    warnings = sum(1 for i in issues if i.severity == "warning")
    infos = sum(1 for i in issues if i.severity == "info")
    return errors, warnings, infos


def _render_summary(filtered: list[Issue], total_result: ScanResult) -> None:
    print("=" * BANNER_WIDTH)
    print(f"  SPAGHETTI CODE DETECTION REPORT (v{_spaghetti_version()})")
    print("=" * BANNER_WIDTH)
    print()
    print(f"  Files scanned:     {total_result.files_scanned}")
    print(f"  Lines scanned:     {total_result.total_lines}")
    print(f"  Functions scanned: {total_result.functions_scanned}")
    print(f"  Total issues:      {len(filtered)}")
    errors, warnings, infos = _severity_counts(filtered)
    print(f"    Errors:          {errors}")
    print(f"    Warnings:        {warnings}")
    print(f"    Info:            {infos}")
    if total_result.suppressed:
        print(f"  Suppressed:        {total_result.suppressed} (inline spaghetti-ignore markers)")
    print()


def _render_scorecard(
    args: argparse.Namespace,
    per_package: dict[str, ScanResult],
    total_result: ScanResult,
) -> None:
    print("=" * BANNER_WIDTH)
    print("  PACKAGE HEALTH SCORECARD")
    print("=" * BANNER_WIDTH)
    print()
    print(f"  {'Package':<16} {'Grade':>5} {'Score':>7}  {'Files':>6} {'KLOC':>6} {'Issues':>7}")
    print(_SCORECARD_DIVIDER)
    for pkg_name in args.packages:
        result = per_package[pkg_name]
        score, grade = compute_score(result)
        kloc = result.total_lines / LINES_PER_KLOC
        print(
            f"  {pkg_name:<16} {grade:>5} {score:>6.1f}  "
            f"{result.files_scanned:>6} {kloc:>6.1f} {len(result.issues):>7}"
        )
    overall_score, overall_grade = compute_score(total_result)
    print(_SCORECARD_DIVIDER)
    print(
        f"  {'OVERALL':<16} {overall_grade:>5} {overall_score:>6.1f}  "
        f"{total_result.files_scanned:>6} {total_result.total_lines / LINES_PER_KLOC:>6.1f} {len(total_result.issues):>7}"
    )
    print()


def _render_affected_files(by_file: dict[str, list[Issue]], top: int) -> None:
    sorted_files = sorted(by_file.items(), key=_file_sort_key)

    print("=" * BANNER_WIDTH)
    print("  AFFECTED FILES")
    print("=" * BANNER_WIDTH)
    print()

    if not sorted_files:
        print("  All clean — no issues found.")
        print()
        return

    print(f"  {'File':<58} {'E':>3} {'W':>3} {'I':>3}  Rules")
    print(_AFFECTED_FILES_DIVIDER)
    for fpath, issues in sorted_files:
        e, w, inf = _severity_counts(issues)
        rules = sorted(set(i.rule for i in issues))
        rules_str = ", ".join(rules)
        if len(rules_str) > _RULES_COL_WIDTH:
            rules_str = rules_str[: _RULES_COL_WIDTH - len("...")] + "..."
        marker = "✖" if e > 0 else "⚠" if w > 0 else "ℹ"
        print(f"  {marker} {fpath:<56} {e:>3} {w:>3} {inf:>3}  {rules_str}")
    print()

    print(f"  Worst files (top {top}):")
    for rank, (fpath, issues) in enumerate(sorted_files[:top], 1):
        e, w, inf = _severity_counts(issues)
        print(f"    {rank}. {fpath} ({len(issues)} issues: {e}E {w}W {inf}I)")
    print()


def _render_cross_file_findings(filtered: list[Issue]) -> None:
    cross_file_rules = {"duplicate-function-body", "sync-async-duplication", "import-cycle"}
    cross_file_issues = [i for i in filtered if i.rule in cross_file_rules]
    if not cross_file_issues:
        return
    print("=" * BANNER_WIDTH)
    print("  CROSS-FILE FINDINGS (duplication & import cycles)")
    print("=" * BANNER_WIDTH)
    print()
    for issue in cross_file_issues:
        print(str(issue))
    print()


def _render_spaghetti_ignored(total_result: ScanResult) -> None:
    if not total_result.ignored:
        return
    print("=" * BANNER_WIDTH)
    print("  SPAGHETTI-IGNORED (inline spaghetti-ignore markers)")
    print("=" * BANNER_WIDTH)
    print()
    sorted_ignored = sorted(total_result.ignored, key=lambda i: (_display_path(i.file), i.line))
    for issue in sorted_ignored:
        icon = _SEVERITY_ICON[issue.severity]
        reason = issue.reason or "no reason given"
        print(f"  {icon} {_display_path(issue.file)}:{issue.line} [{issue.rule}] {reason}")
    print()


def _render_detailed_findings(args: argparse.Namespace, by_package: dict[str, list[Issue]]) -> None:
    print("=" * BANNER_WIDTH)
    print("  DETAILED FINDINGS")
    print("=" * BANNER_WIDTH)
    print()

    for pkg_name in args.packages:
        pkg_issues = [i for i in by_package.get(pkg_name, [])]
        if not pkg_issues:
            print(f"  ✅ {pkg_name}: clean")
            print()
            continue

        errors, warnings, infos = _severity_counts(pkg_issues)

        status = "✖" if errors > 0 else "⚠" if warnings > 0 else "✓"
        print(f"  {status} {pkg_name}: {len(pkg_issues)} issues ({errors}E {warnings}W {infos}I)")
        print("-" * BANNER_WIDTH)

        pkg_files: dict[str, list[Issue]] = defaultdict(list)
        for issue in pkg_issues:
            key = _display_path(issue.file)
            pkg_files[key].append(issue)

        sorted_pkg_files = sorted(pkg_files.items(), key=_file_sort_key)

        for fpath, file_issues in sorted_pkg_files:
            fe, fw, fi = _severity_counts(file_issues)
            status = "✖" if fe > 0 else "⚠" if fw > 0 else "ℹ"
            print(f"    {status} {fpath} ({len(file_issues)} issues: {fe}E {fw}W {fi}I)")

            for issue in file_issues:
                icon = _SEVERITY_ICON[issue.severity]
                print(f"      {icon} L{issue.line:<5} [{issue.rule}] {issue.message}")
        print()


def _render_rule_summary(filtered: list[Issue]) -> None:
    rule_counts: dict[str, int] = defaultdict(int)
    rule_by_severity: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for issue in filtered:
        rule_counts[issue.rule] += 1
        rule_by_severity[issue.rule][issue.severity] += 1

    print("=" * BANNER_WIDTH)
    print("  RULE SUMMARY")
    print("=" * BANNER_WIDTH)
    print(f"  {'Rule':<30} {'Total':>5} {'E':>3} {'W':>3} {'I':>3}")
    print(_RULE_SUMMARY_DIVIDER)
    for rule_name, count in sorted(rule_counts.items(), key=lambda x: -x[1]):
        rs = rule_by_severity[rule_name]
        print(f"  {rule_name:<30} {count:>5} {rs['error']:>3} {rs['warning']:>3} {rs['info']:>3}")
    print()


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

    _render_summary(filtered, total_result)
    _render_scorecard(args, per_package, total_result)
    _render_affected_files(by_file, args.top)
    _render_cross_file_findings(filtered)
    _render_spaghetti_ignored(total_result)
    _render_detailed_findings(args, by_package)
    _render_rule_summary(filtered)


if __name__ == "__main__":
    sys.exit(main())
