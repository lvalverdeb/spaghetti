"""Every rule has documentation, and docs/rules/ is generated from it."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import spaghetti.config as _cfg
from spaghetti.rule_docs import RULE_DOCS
from spaghetti.rules import RULE_CATALOG

ROOT = Path(__file__).resolve().parent.parent


def test_every_rule_is_documented_and_nothing_else():
    assert set(RULE_DOCS) == set(RULE_CATALOG)
    for rule, doc in RULE_DOCS.items():
        assert doc.why.endswith(".") and doc.fix.endswith("."), rule
        for name in doc.thresholds:
            assert hasattr(_cfg, name), (rule, name)


def test_generated_rule_pages_are_current():
    proc = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "gen_rule_docs.py"), "--check"],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, "docs/rules is stale; run scripts/gen_rule_docs.py\n" + proc.stderr
