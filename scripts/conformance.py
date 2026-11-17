#!/usr/bin/env python3
"""Python <-> Rust conformance: the two ports must produce byte-identical reports.

    uv run python scripts/conformance.py --rust ../spaghetti-rs/target/release/spaghetti
    uv run python scripts/conformance.py --rust BIN --set audit      # the five audit repos only
    uv run python scripts/conformance.py --rust BIN --self           # this repo's own source only

For every repo in scripts/corpus.lock (checked out shallowly under
.spaghetti/corpus or $SPAGHETTI_CORPUS) both binaries run `--json` and
`--plan --json`; the outputs are compared after normalising the tool version.
One more case copies this repo's own source into a temporary uv workspace and
scans it from there by a *relative* `--package` path, so the workspace-relative
display of file paths (inside messages too) is compared as well; the corpus
cases all pass absolute paths from a directory with no workspace, which is how
a path-display difference between the ports once went unnoticed.
Exit 1 on any difference, with a unified diff of the first one.
"""

from __future__ import annotations

import argparse
import difflib
import json
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from corpus import DEFAULT_CORPUS_DIR, ROOT, ensure_checkout, load_lock  # noqa: E402

_VERSION = re.compile(r'"version": "[^"]+"')
MODES = {"report": ["--json"], "plan": ["--plan", "--json"]}


@dataclass(frozen=True)
class Case:
    """One `--package NAME=PATH` scan, run from `cwd`."""

    name: str
    path: str
    cwd: Path
    relative_paths: bool = False  # the report must show workspace-relative files
    expects: tuple[str, ...] = ()  # rules the report must contain, so the case is not vacuous


# Two copies of this body give the package a duplicate-function-body finding,
# whose message quotes file paths; this repo's own source has none.
_TWIN = """\
def shared(items):
    total = 0
    for item in items:
        if item is None:
            continue
        total += item
        if total > 100:
            break
    return total
"""


def _workspace_case(tmp: Path) -> Case:
    """This repo's source inside a fresh uv workspace, addressed by a relative path."""
    workspace = tmp / "workspace"
    workspace.mkdir()
    (workspace / "pyproject.toml").write_text("[tool.uv.workspace]\nmembers = []\n")
    package = workspace / "pkg" / "spaghetti"
    shutil.copytree(ROOT / "src" / "spaghetti", package)
    (package / "_conformance").mkdir()
    (package / "_conformance" / "__init__.py").write_text("")
    (package / "_conformance" / "first.py").write_text(_TWIN)
    (package / "_conformance" / "second.py").write_text(_TWIN)
    return Case(
        "workspace",
        "pkg/spaghetti",
        workspace.resolve(),
        relative_paths=True,
        expects=("duplicate-function-body", "duplicate-code"),
    )


def _run(binary: str, case: Case, mode: list[str]) -> str:
    argv = [
        binary,
        "--no-pyproject",
        "--fail-on",
        "never",
        "--quiet",
        *mode,
        "--package",
        f"{case.name}={case.path}",
    ]
    proc = subprocess.run(argv, capture_output=True, text=True, cwd=case.cwd)
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
    # Cases run from other directories, so the binaries must be absolute.
    python, rust = str(Path(args.python).resolve()), str(Path(args.rust).resolve())

    with tempfile.TemporaryDirectory() as tmp:
        cases = [
            Case("spaghetti", str(ROOT / "src" / "spaghetti"), ROOT),
            _workspace_case(Path(tmp)),
        ]
        if not args.self:
            for repo in load_lock(args.set):
                ensure_checkout(repo, args.corpus)
                cases.append(Case(repo.name, str(repo.package_root(args.corpus)), ROOT))
        failures = sum(_compare(python, rust, case) for case in cases)
    print(f"{len(cases) * len(MODES) - failures} identical, {failures} different")
    return 1 if failures else 0


def _compare(python: str, rust: str, case: Case) -> int:
    """Run both ports on `case` in every mode; return the number of differences."""
    failures = 0
    for mode_name, mode in MODES.items():
        py, rs = _run(python, case, mode), _run(rust, case, mode)
        if py == rs and not py.startswith("<no JSON"):
            issues = json.loads(py)["issues"]
            if case.relative_paths and any(Path(i["file"]).is_absolute() for i in issues):
                failures += 1
                print(f"DIFF  {case.name:<10} {mode_name}  (paths are not workspace-relative)")
                continue
            if missing := set(case.expects) - {i["rule"] for i in issues}:
                failures += 1
                print(f"DIFF  {case.name:<10} {mode_name}  (missing {', '.join(sorted(missing))})")
                continue
            print(f"ok    {case.name:<10} {mode_name:<7} {len(issues)} findings")
            continue
        failures += 1
        print(f"DIFF  {case.name:<10} {mode_name}  {_order_note(py, rs)}")
        diff = difflib.unified_diff(
            py.splitlines(), rs.splitlines(), "python", "rust", lineterm="", n=2
        )
        print("\n".join(list(diff)[:60]))
    return failures


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
