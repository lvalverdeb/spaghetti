#!/usr/bin/env python3
"""Phase 5 performance budget: time a scan of the benchmark corpus.

    uv run python scripts/bench.py                        # Python binary, print the table
    uv run python scripts/bench.py --rust ../spaghetti-rs/target/release/spaghetti
    uv run python scripts/bench.py --budget 90            # exit 1 if the combined Python run is slower
    uv run python scripts/bench.py --write docs/benchmark.md

The corpus (django, pandas, airflow; see scripts/corpus.lock) is checked out
shallowly under .spaghetti/corpus or $SPAGHETTI_CORPUS. Each repo is scanned
alone and all three together — the combined run is what the budget applies to,
since the Python port scans packages in parallel processes. With --cache the
Python port is also run twice to show the warm-cache time.
"""

from __future__ import annotations

import argparse
import json
import platform
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from corpus import (  # noqa: E402
    DEFAULT_CORPUS_DIR,
    ROOT,
    Repo,
    count_lines,
    ensure_checkout,
    load_lock,
)

BASE_ARGS = ["--no-pyproject", "--fail-on", "never", "--json", "--quiet"]


def _run(
    binary: list[str], packages: list[tuple[str, Path]], extra: list[str]
) -> tuple[float, dict]:
    argv = [*binary, *BASE_ARGS, *extra]
    for name, root in packages:
        argv += ["--package", f"{name}={root}"]
    start = time.perf_counter()
    proc = subprocess.run(argv, capture_output=True, text=True)
    elapsed = time.perf_counter() - start
    if proc.returncode not in (0, 1):
        sys.exit(f"{' '.join(argv)} failed:\n{proc.stderr}")
    return elapsed, json.loads(proc.stdout)


def _best(
    binary: list[str], packages: list[tuple[str, Path]], runs: int, extra: list[str]
) -> tuple[float, dict]:
    best, report = None, {}
    for _ in range(runs):
        elapsed, report = _run(binary, packages, extra)
        best = elapsed if best is None else min(best, elapsed)
    return best or 0.0, report


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--python", default=None, help="Python spaghetti binary (default: the venv's)")
    ap.add_argument("--rust", default=None, help="Rust spaghetti binary (optional)")
    ap.add_argument("--corpus", type=Path, default=DEFAULT_CORPUS_DIR)
    ap.add_argument("--runs", type=int, default=1, help="Best of N (default 1)")
    ap.add_argument(
        "--budget",
        type=float,
        default=None,
        help="Fail if the combined Python run exceeds this many seconds",
    )
    ap.add_argument("--rust-budget", type=float, default=None, help="Same for the Rust binary")
    ap.add_argument(
        "--cache", action="store_true", help="Also time a warm --cache run of the Python port"
    )
    ap.add_argument("--write", type=Path, default=None, help="Write the markdown table here")
    args = ap.parse_args(argv)

    python = [args.python] if args.python else [str(ROOT / ".venv" / "bin" / "spaghetti")]
    repos = load_lock("bench")
    roots = {r.name: r.package_root(args.corpus) for r in repos}
    for r in repos:
        ensure_checkout(r, args.corpus)

    rows: list[dict] = []
    all_packages = [(r.name, roots[r.name]) for r in repos]
    for r in repos:
        rows.append(_row(r, [(r.name, roots[r.name])], python, args))
    rows.append(_row(None, all_packages, python, args))

    table = _render(rows, args, repos)
    print(table)
    if args.write:
        args.write.write_text(table)
        print(f"wrote {args.write}", file=sys.stderr)

    failed = False
    combined = rows[-1]
    if args.budget is not None and combined["python"] > args.budget:
        print(
            f"BUDGET EXCEEDED: Python {combined['python']:.1f}s > {args.budget}s", file=sys.stderr
        )
        failed = True
    if args.rust_budget is not None and combined.get("rust", 0) > args.rust_budget:
        print(
            f"BUDGET EXCEEDED: Rust {combined['rust']:.1f}s > {args.rust_budget}s", file=sys.stderr
        )
        failed = True
    return 1 if failed else 0


def _row(repo: Repo | None, packages: list[tuple[str, Path]], python: list[str], args) -> dict:
    row: dict = {
        "name": repo.name if repo else "all three",
        "lines": sum(count_lines(p) for _, p in packages),
    }
    elapsed, report = _best(python, packages, args.runs, [])
    row.update(python=elapsed, files=report["summary"]["files"], issues=len(report["issues"]))
    if args.cache:
        with tempfile.TemporaryDirectory() as tmp:
            _run(python, packages, ["--cache-dir", tmp])
            row["python_warm"], _ = _best(python, packages, args.runs, ["--cache-dir", tmp])
    if args.rust:
        row["rust"], rust_report = _best([args.rust], packages, args.runs, [])
        row["rust_issues"] = len(rust_report["issues"])
    return row


def _render(rows: list[dict], args, repos: list[Repo]) -> str:
    head = ["Package", "Files", "Lines", "Findings", "Python (s)"]
    if args.cache:
        head.append("Python, warm cache (s)")
    if args.rust:
        head += ["Rust (s)", "Rust findings"]
    lines = [
        "# Benchmark",
        "",
        f"Generated {datetime.now(UTC).strftime('%Y-%m-%d')} by `scripts/bench.py` on "
        f"{platform.machine()} ({platform.system()}), Python {platform.python_version()}, "
        f"{_cpu_count()} CPUs, best of {args.runs}. Corpus pinned in `scripts/corpus.lock`: "
        + ", ".join(f"{r.name}@{r.commit[:7]}" for r in repos)
        + ".",
        "",
        "| " + " | ".join(head) + " |",
        "|" + "---|" * len(head),
    ]
    for row in rows:
        cells = [
            row["name"],
            str(row["files"]),
            f"{row['lines']:,}",
            str(row["issues"]),
            f"{row['python']:.1f}",
        ]
        if args.cache:
            cells.append(f"{row['python_warm']:.1f}")
        if args.rust:
            cells += [f"{row['rust']:.1f}", str(row["rust_issues"])]
        lines.append("| " + " | ".join(cells) + " |")
    lines += [
        "",
        "Budget (ROADMAP Phase 5): the *all three* row under 90 s for Python and 15 s for Rust "
        "on the 4-core GitHub runner, which is about 1.7× slower per core than the laptop above. "
        "CI enforces the budgets with `--budget 90` and `--rust-budget 15`.",
        "",
    ]
    return "\n".join(lines)


def _cpu_count() -> int:
    import os

    return os.cpu_count() or 0


if __name__ == "__main__":
    if shutil.which("git") is None:
        sys.exit("git is required")
    sys.exit(main(sys.argv[1:]))
