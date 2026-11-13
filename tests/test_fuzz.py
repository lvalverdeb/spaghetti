"""Robustness: the scanner never raises and always yields a report.

Default runs use a few dozen generated programs; CI's fuzz job sets
``SPAGHETTI_FUZZ_EXAMPLES`` high enough to run for about ten minutes.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import hypothesmith
import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from spaghetti import detector as ds
from spaghetti.models import ScanConfig

_CONFIG = ScanConfig(exclude=[], min_duplicate_lines=5, twin_similarity=0.6)
_EXAMPLES = int(os.environ.get("SPAGHETTI_FUZZ_EXAMPLES", "25"))


def _scan_text(text: str, name: str = "m.py") -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "pkg"
        root.mkdir()
        (root / name).write_text(text, encoding="utf-8", errors="surrogateescape")
        saved = ds.PACKAGES
        ds.PACKAGES = {"pkg": root}
        try:
            per = {"pkg": ds.scan_package("pkg", root, config=_CONFIG)}
            ds.scan_workspace(per, config=_CONFIG)
        finally:
            ds.PACKAGES = saved
        result = per["pkg"]
        assert result.files_scanned == 1
        for issue in result.issues:
            assert issue.rule and issue.fingerprint and issue.line >= 1


@settings(
    max_examples=_EXAMPLES,
    deadline=None,
    suppress_health_check=[
        HealthCheck.too_slow,
        HealthCheck.data_too_large,
        HealthCheck.filter_too_much,
    ],
)
@given(source=hypothesmith.from_grammar() | hypothesmith.from_node())
def test_scanner_never_raises_on_generated_programs(source: str):
    _scan_text(source)


@settings(max_examples=_EXAMPLES, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(text=st.text())
def test_scanner_never_raises_on_arbitrary_text(text: str):
    _scan_text(text)


@pytest.mark.parametrize(
    "text",
    [
        "",
        "\ufeffimport os\n",
        "x = 1\r\ny = 2\r\n",
        "x = '\x00'\n",
        "def f():\n\tif 1:\n\t\treturn\n",
        "x = 1\n" + "if x:\n" * 150 + "    pass\n",
        "x = " + "[" * 3000 + "]" * 3000 + "\n",
        "class A(" + ",".join(f"B{i}" for i in range(500)) + "): pass\n",
        "lambda: " * 300 + "0\n",
        "f(" + ", ".join(f"a{i}" for i in range(2000)) + ")\n",
        "x = " + " + ".join(["1"] * 5000) + "\n",
        'x: "\\ud83d" = 1\n',  # lone surrogate escape in a string annotation (pandas)
        'def f(a: "\\udcff") -> "List[\\ud800]": ...\n',
    ],
)
def test_scanner_survives_known_edge_cases(text: str):
    _scan_text(text)
