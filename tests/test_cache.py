"""Incremental cache: entries round-trip, hits skip the checks, keys invalidate."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import spaghetti.config as _cfg
from spaghetti import detector as ds
from spaghetti.cache import FileCache, cache_key
from spaghetti.models import ScanConfig

SOURCE = (
    "import os\n"
    "def f(a, b, c, d, e, f, g, h):\n"
    "    return a\n"
    "class K:\n"
    "    def m(self):\n"
    "        return 1\n"
)


def _config(tmp_path: Path, key: str = "k") -> ScanConfig:
    return ScanConfig(
        exclude=[],
        min_duplicate_lines=5,
        twin_similarity=0.6,
        cache_dir=tmp_path / "cache",
        cache_key=key,
    )


@pytest.fixture
def pkg(tmp_path: Path, monkeypatch) -> Path:
    root = tmp_path / "pkg"
    root.mkdir()
    (root / "m.py").write_text(SOURCE)
    (root / "bad.py").write_text("def (:\n")
    monkeypatch.setattr(ds, "PACKAGES", {"pkg": root})
    return root


def _scan(root: Path, config: ScanConfig):
    per = {"pkg": ds.scan_package("pkg", root, config=config)}
    ds.scan_workspace(per, config=config)
    return per["pkg"]


def test_cache_hit_reproduces_the_uncached_result_exactly(pkg: Path, tmp_path: Path):
    plain = _scan(pkg, ScanConfig(exclude=[], min_duplicate_lines=5, twin_similarity=0.6))
    cold = _scan(pkg, _config(tmp_path))
    warm = _scan(pkg, _config(tmp_path))
    assert (cold.cache_hits, cold.cache_misses) == (0, 2)
    assert (warm.cache_hits, warm.cache_misses) == (2, 0)
    for result in (cold, warm):
        assert result.issues == plain.issues
        assert result.file_mi == plain.file_mi
        assert result.functions_scanned == plain.functions_scanned
        assert result.total_lines == plain.total_lines
    entries = list((tmp_path / "cache" / "k").glob("*.json"))
    assert len(entries) == 2
    data = json.loads(entries[0].read_text())
    assert data["schema_version"] == 1 and "issues" in data


def test_edited_file_misses_and_changed_key_invalidates(pkg: Path, tmp_path: Path):
    _scan(pkg, _config(tmp_path))
    (pkg / "m.py").write_text(SOURCE + "x = 1\n")
    result = _scan(pkg, _config(tmp_path))
    assert (result.cache_hits, result.cache_misses) == (1, 1)
    result = _scan(pkg, _config(tmp_path, key="other"))
    assert (result.cache_hits, result.cache_misses) == (0, 2)


def test_corrupt_or_foreign_entries_are_misses(pkg: Path, tmp_path: Path):
    _scan(pkg, _config(tmp_path))
    for entry in (tmp_path / "cache" / "k").glob("*.json"):
        entry.write_text("{not json")
    result = _scan(pkg, _config(tmp_path))
    assert (result.cache_hits, result.cache_misses) == (0, 2)
    for entry in (tmp_path / "cache" / "k").glob("*.json"):
        entry.write_text(json.dumps({"schema_version": 99}))
    assert _scan(pkg, _config(tmp_path)).cache_hits == 0


def test_cache_key_covers_settings_tool_and_python_version():
    base = cache_key({"thresholds": {"MAX_FUNCTION_LINES": 50}}, "2.3.0")
    assert len(base) == 16
    assert cache_key({"thresholds": {"MAX_FUNCTION_LINES": 51}}, "2.3.0") != base
    assert cache_key({"thresholds": {"MAX_FUNCTION_LINES": 50}}, "2.3.1") != base
    assert cache_key({"thresholds": {"MAX_FUNCTION_LINES": 50}}, "2.3.0") == base


def test_unwritable_cache_dir_is_silent(pkg: Path, tmp_path: Path):
    blocker = tmp_path / "cache"
    blocker.write_text("not a directory")
    result = _scan(pkg, _config(tmp_path))
    assert (result.cache_hits, result.cache_misses) == (0, 2)
    assert FileCache(blocker, "k").load(SOURCE, file=pkg / "m.py", package="pkg") is None


def test_scan_limits_emit_an_info_finding_and_skip_the_file(tmp_path: Path, monkeypatch):
    root = tmp_path / "pkg"
    root.mkdir()
    (root / "big.py").write_text("x = 1\n" * 2000)
    (root / "deep.py").write_text("x = a" + ".b" * 3000 + "\n")  # recursion in the checks
    monkeypatch.setattr(ds, "PACKAGES", {"pkg": root})
    monkeypatch.setattr(_cfg, "MAX_FILE_BYTES", 7000)
    result = _scan(root, ScanConfig(exclude=[], min_duplicate_lines=5, twin_similarity=0.6))
    by_file = {i.file.name: i for i in result.issues if i.rule == "scan-limit"}
    assert set(by_file) == {"big.py", "deep.py"}
    assert "12000 bytes exceeds max-file-bytes (7000)" in by_file["big.py"].message
    assert "nesting deeper" in by_file["deep.py"].message
    assert all(i.severity == "info" for i in by_file.values())
    assert result.files_scanned == 2 and result.total_lines == 2001
    assert not [i for i in result.issues if i.rule != "scan-limit"]
