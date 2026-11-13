#!/usr/bin/env python3
"""CI gate: spaghetti scanned by spaghetti must stay error-free and graded B or better.

Runs the installed ``spaghetti`` on ``src/spaghetti`` with ``--json`` and fails
(exit 1) if any error-level finding survives or the grade drops below
``MIN_GRADE``. Prints the offending findings so the failure is actionable.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

MIN_GRADE = "B"
_GRADE_ORDER = "ABCDF"

REPO = Path(__file__).resolve().parents[1]


def main() -> int:
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "spaghetti.detector",
            "--package",
            f"spaghetti={REPO / 'src' / 'spaghetti'}",
            "--json",
        ],
        capture_output=True,
        text=True,
        cwd=REPO,
    )
    if proc.returncode not in (0, 1, 2):
        sys.stderr.write(proc.stderr)
        return 1
    report = json.loads(proc.stdout)

    stats = report["packages"]["spaghetti"]
    errors = [i for i in report["issues"] if i["severity"] == "error"]
    failed = False

    print(
        f"spaghetti on itself: grade {stats['grade']} ({stats['score']}), {stats['issues']} issues"
    )
    if _GRADE_ORDER.index(stats["grade"]) > _GRADE_ORDER.index(MIN_GRADE):
        print(f"FAIL: grade {stats['grade']} is below the {MIN_GRADE} floor")
        failed = True
    if errors:
        print(f"FAIL: {len(errors)} error-level finding(s):")
        for i in errors:
            print(f"  {i['file']}:{i['line']} [{i['rule']}] {i['message']}")
        failed = True
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
