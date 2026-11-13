#!/usr/bin/env python3
"""Python <-> Rust conformance: the two ports must produce byte-identical reports.

    uv run python scripts/conformance.py --rust ../spaghetti-rs/target/release/spaghetti
    uv run python scripts/conformance.py --rust BIN --set audit      # the five audit repos only
    uv run python scripts/conformance.py --rust BIN --self           # this repo's own source only

For every repo in scripts/corpus.lock (checked out shallowly under
.spaghetti/corpus or $SPAGHETTI_CORPUS) both binaries run `--json` and
`--plan --json`; the outputs are compared after normalising the tool version.
Exit 1 on any difference, with a unified diff of the first one.
"""

from __future__ import annotations

import argparse
import difflib
import json
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from corpus import DEFAULT_CORPUS_DIR, ROOT, ensure_checkout, load_lock  # noqa: E402

_VERSION = re.compile(r'"version": "[^"]+"')
MODES = {"report": ["--json"], "plan": ["--plan", "--json"]}


def _run(binary: str, package: tuple[str, Path], mode: list[str]) -> str:
    argv = [
        binary,
        "--no-pyproject",
        "--fail-on",
        "never",
        "--quiet",
        *mode,
        "--package",
        f"{package[0]}={package[1]}",
    ]
    proc = subprocess.run(argv, capture_output=True, text=True, cwd=ROOT)
    try:
        report = json.loads(proc.stdout)
    except json.JSONDecodeError:
        tail = "\n".join(proc.stderr.strip().splitlines()[-8:])
        return f"<no JSON report: exit {proc.returncode}>\n{tail}"
    return _VERSION.sub('"version": "X"', json.dumps(report, indent=1, sort_keys=True))


def _order_note(py: str, rs: str) -> str:
    """Whether the two reports hold the same findings in a different order."""
    try:
        a, b = json.loads(py), json.loads(rs)
    except json.JSONDecodeError:
        return "(one side produced no report)"
    same = sorted(map(json.dumps, a["issues"])) == sorted(map(json.dumps, b["issues"]))
    return "(same findings, different order)" if same else "(different findings)"


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--rust", required=True, help="Rust spaghetti binary")
    ap.add_argument("--python", default=str(ROOT / ".venv" / "bin" / "spaghetti"))
    ap.add_argument("--corpus", type=Path, default=DEFAULT_CORPUS_DIR)
    ap.add_argument(
        "--set", choices=["bench", "audit"], default=None, help="Restrict to one corpus set"
    )
    ap.add_argument("--self", action="store_true", help="Only scan this repository's src/spaghetti")
    args = ap.parse_args(argv)

    packages: list[tuple[str, Path]] = [("spaghetti", ROOT / "src" / "spaghetti")]
    if not args.self:
        for repo in load_lock(args.set):
            ensure_checkout(repo, args.corpus)
            packages.append((repo.name, repo.package_root(args.corpus)))

    failures = 0
    for package in packages:
        for mode_name, mode in MODES.items():
            py, rs = _run(args.python, package, mode), _run(args.rust, package, mode)
            if py == rs and not py.startswith("<no JSON"):
                findings = len(json.loads(py)["issues"])
                print(f"ok    {package[0]:<10} {mode_name:<7} {findings} findings")
                continue
            failures += 1
            print(f"DIFF  {package[0]:<10} {mode_name}  {_order_note(py, rs)}")
            diff = difflib.unified_diff(
                py.splitlines(), rs.splitlines(), "python", "rust", lineterm="", n=2
            )
            print("\n".join(list(diff)[:60]))
    print(f"{len(packages) * len(MODES) - failures} identical, {failures} different")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
