"""Tests for the workspace-level pass: cross-package cycles, coupling and clones
(spaghetti.checks.workspace_level + detector.scan_workspace)."""

from __future__ import annotations

from pathlib import Path

import pytest

from spaghetti import detector as ds
from spaghetti.models import ScanConfig

_CLONE = """
def {name}(items, limit):
    total = 0
    seen = set()
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        if item.value > limit:
            total += item.value * 2
        else:
            total -= 1
    return total, len(seen)
"""


def _workspace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, packages: dict[str, dict[str, str]]
):
    registry: dict[str, Path] = {}
    for pkg, files in packages.items():
        root = tmp_path / pkg
        root.mkdir()
        for name, src in files.items():
            (root / name).write_text(src)
        registry[pkg] = root
    monkeypatch.setattr(ds, "PACKAGES", registry)
    config = ScanConfig(exclude=[], min_duplicate_lines=5, twin_similarity=0.6, min_clone_tokens=30)
    per_package = {pkg: ds.scan_package(pkg, root, config=config) for pkg, root in registry.items()}
    ds.scan_workspace(per_package, config=config)
    return per_package


def test_cycle_spanning_two_packages_is_reported_once(tmp_path, monkeypatch):
    per = _workspace(
        tmp_path,
        monkeypatch,
        {
            "alpha": {"__init__.py": "", "a.py": "from beta.b import B\n"},
            "beta": {"__init__.py": "", "b.py": "from alpha.a import A\n"},
        },
    )
    cycles = [i for r in per.values() for i in r.issues if i.rule == "import-cycle"]
    assert len(cycles) == 1
    assert "alpha.a → beta.b → alpha.a" in cycles[0].message
    assert cycles[0].package == "alpha"
    assert cycles[0].fingerprint and cycles[0].file == tmp_path / "alpha" / "a.py"


def test_clone_across_packages_reported_on_both_sides(tmp_path, monkeypatch):
    per = _workspace(
        tmp_path,
        monkeypatch,
        {
            "alpha": {"__init__.py": "", "a.py": _CLONE.format(name="f")},
            "beta": {"__init__.py": "", "b.py": _CLONE.format(name="g").replace("item", "row")},
        },
    )
    clones = [i for r in per.values() for i in r.issues if i.rule == "duplicate-code"]
    assert [i.package for i in clones] == ["alpha", "beta"]  # both sides are told
    assert "Lines 2-13 are duplicated in 1 other place(s)" in clones[0].message
    assert "b.py:2-13" in clones[0].message and "a.py:2-13" in clones[1].message
    assert clones[0].line == 2 and clones[0].end_line == 13


def test_workspace_findings_honour_suppressions_and_overrides(tmp_path, monkeypatch):
    import spaghetti.config as _cfg

    monkeypatch.setattr(_cfg, "RULE_OVERRIDES", {"duplicate-code": "off"})
    per = _workspace(
        tmp_path,
        monkeypatch,
        {
            "alpha": {
                "__init__.py": "",
                "a.py": "# spaghetti-ignore[import-cycle]: legacy\nfrom alpha.b import B\n",
                "b.py": "from alpha.a import A\n" + _CLONE.format(name="f"),
                "c.py": _CLONE.format(name="g"),
            }
        },
    )
    result = per["alpha"]
    assert all(i.rule not in ("import-cycle", "duplicate-code") for i in result.issues)
    assert [i.rule for i in result.ignored] == ["import-cycle"]
    assert result.suppressed == 1
    assert result.raw_imports == {} and result.token_streams == {}


def test_maintainability_index_is_recorded_per_file(tmp_path, monkeypatch):
    per = _workspace(tmp_path, monkeypatch, {"alpha": {"__init__.py": "", "a.py": "x = 1\n"}})
    result = per["alpha"]
    assert set(result.file_mi) == {tmp_path / "alpha" / "__init__.py", tmp_path / "alpha" / "a.py"}
    assert result.maintainability_index == 100.0
