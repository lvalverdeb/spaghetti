"""
Tests for spaghetti.detector.

spaghetti is an installed workspace package, so detector is imported
normally here rather than via a sys.path insertion. Run with:

    uv run pytest spaghetti/tests/test_detector.py

Covers:
  - scan_package() end-to-end against a small synthetic package on disk,
    including syntax-error / unreadable-file handling, issue locations and
    fingerprints
  - The concurrent review orchestration (review_packages_concurrently):
    correctness, genuine concurrency (proven deterministically via a
    threading.Barrier rather than wall-clock timing), and exception
    propagation when one package's scan fails.
  - The workspace-root discovery regression in spaghetti.config that used
    to break importing spaghetti.detector outside a uv workspace.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import os
import sys
import threading
from pathlib import Path

import pytest

from spaghetti import config as _cfg
from spaghetti import detector as ds
from spaghetti.models import ScanConfig, ScanResult

# ── Workspace root discovery ─────────────────────────────────────────────────


def test_find_workspace_root_returns_none_when_absent(tmp_path):
    # Regression test: this used to be a hard RuntimeError raised at import
    # time, which broke importing spaghetti.config (and therefore
    # spaghetti.detector, and this whole test module) in any standalone
    # checkout with no ancestor [tool.uv.workspace] — exactly what
    # spaghetti's own GitHub Actions checkout looks like.
    from spaghetti.config import _find_workspace_root

    assert _find_workspace_root(tmp_path) is None


def test_find_workspace_root_finds_ancestor_workspace_marker(tmp_path):
    from spaghetti.config import _find_workspace_root

    (tmp_path / "pyproject.toml").write_text("[tool.uv.workspace]\nmembers = []\n")
    nested = tmp_path / "a" / "b"
    nested.mkdir(parents=True)
    assert _find_workspace_root(nested) == tmp_path


# ── scan_package() end-to-end ────────────────────────────────────────────────


def test_scan_package_finds_issues_in_synthetic_package(fake_package: Path):
    result = ds.scan_package(
        "fake_pkg",
        fake_package,
        config=ScanConfig(exclude=[], min_duplicate_lines=5, twin_similarity=0.6),
    )
    assert result.files_scanned == 2
    assert any(i.rule == "bare-except" for i in result.issues)
    assert all(i.package == "fake_pkg" for i in result.issues)


def test_scan_package_missing_path_returns_empty_result(tmp_path: Path):
    result = ds.scan_package(
        "missing",
        tmp_path / "does-not-exist",
        config=ScanConfig(exclude=[], min_duplicate_lines=5, twin_similarity=0.6),
    )
    assert result.files_scanned == 0
    assert result.issues == []


def test_scan_package_respects_exclude(fake_package: Path):
    result = ds.scan_package(
        "fake_pkg",
        fake_package,
        config=ScanConfig(exclude=["messy.py"], min_duplicate_lines=5, twin_similarity=0.6),
    )
    assert result.files_scanned == 1
    assert not any(i.rule == "bare-except" for i in result.issues)


def test_scan_package_non_recursive_ignores_subdirectories(tmp_path: Path):
    (tmp_path / "loose.py").write_text(
        "def f():\n    try:\n        pass\n    except:\n        pass\n"
    )
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "mod.py").write_text(
        "def g():\n    try:\n        pass\n    except:\n        pass\n"
    )

    result = ds.scan_package(
        "root",
        tmp_path,
        config=ScanConfig(exclude=[], min_duplicate_lines=5, twin_similarity=0.6, recursive=False),
    )

    assert result.files_scanned == 1
    assert all(i.file == tmp_path / "loose.py" for i in result.issues if i.rule == "bare-except")


# ── review_packages_concurrently() ───────────────────────────────────────────


def test_review_packages_concurrently_returns_correct_mapping(
    monkeypatch: pytest.MonkeyPatch, fake_package: Path
):
    monkeypatch.setattr(ds, "PACKAGES", {"a": fake_package, "b": fake_package})

    executor = concurrent.futures.ThreadPoolExecutor(max_workers=2)
    try:
        results = asyncio.run(
            ds.review_packages_concurrently(
                ["a", "b"],
                config=ScanConfig(exclude=[], min_duplicate_lines=5, twin_similarity=0.6),
                executor=executor,
            )
        )
    finally:
        executor.shutdown(wait=False)
    assert set(results) == {"a", "b"}
    assert all(isinstance(r, ScanResult) for r in results.values())
    assert all(r.files_scanned == 2 for r in results.values())


def test_review_packages_concurrently_actually_overlaps(monkeypatch: pytest.MonkeyPatch):
    """Deterministic proof of concurrency: if the packages were reviewed one
    at a time, only one call would ever be inside fake_scan_package
    simultaneously and the barrier would never reach `n` parties, timing out.
    This fails loudly (BrokenBarrierError) if a future change accidentally
    serializes the reviews instead of running them concurrently."""
    n = 5
    all_arrived = threading.Barrier(n, timeout=5)

    def fake_scan_package(pkg_name, pkg_path, *, config):
        all_arrived.wait()
        return ScanResult(files_scanned=1)

    monkeypatch.setattr(ds, "scan_package", fake_scan_package)
    pkg_names = [f"pkg{i}" for i in range(n)]
    monkeypatch.setattr(ds, "PACKAGES", {name: Path(f"/fake/{name}") for name in pkg_names})

    executor = concurrent.futures.ThreadPoolExecutor(max_workers=n)
    try:
        results = asyncio.run(
            ds.review_packages_concurrently(
                pkg_names,
                config=ScanConfig(exclude=[], min_duplicate_lines=5, twin_similarity=0.6),
                executor=executor,
            )
        )
    finally:
        executor.shutdown(wait=False)
    assert set(results) == set(pkg_names)


def test_review_packages_concurrently_propagates_a_failing_package(
    monkeypatch: pytest.MonkeyPatch,
):
    """A package whose scan raises must surface the error to the caller
    rather than silently dropping that package from the report — the same
    fail-fast behavior the old sequential loop had."""

    def flaky_scan_package(pkg_name, pkg_path, *, config):
        if pkg_name == "broken":
            raise RuntimeError("scan exploded")
        return ScanResult(files_scanned=1)

    monkeypatch.setattr(ds, "scan_package", flaky_scan_package)
    monkeypatch.setattr(ds, "PACKAGES", {"ok": Path("/fake/ok"), "broken": Path("/fake/broken")})

    executor = concurrent.futures.ThreadPoolExecutor(max_workers=2)
    try:
        with pytest.raises(RuntimeError, match="scan exploded"):
            asyncio.run(
                ds.review_packages_concurrently(
                    ["ok", "broken"],
                    config=ScanConfig(exclude=[], min_duplicate_lines=5, twin_similarity=0.6),
                    executor=executor,
                )
            )
    finally:
        executor.shutdown(wait=False)


# ── Syntax Error Handling ────────────────────────────────────────────────────


def test_scan_package_handles_syntax_error(tmp_path: Path):
    pkg_dir = tmp_path / "syntax_err_pkg"
    pkg_dir.mkdir()
    (pkg_dir / "broken.py").write_text("def f(\n")
    result = ds.scan_package(
        "syntax_err_pkg",
        pkg_dir,
        config=ScanConfig(exclude=[], min_duplicate_lines=5, twin_similarity=0.6),
    )
    assert result.files_scanned == 1
    assert any(i.rule == "syntax-error" for i in result.issues)


def _scan_one(pkg_dir: Path) -> ds.ScanResult:
    return ds.scan_package(
        pkg_dir.name,
        pkg_dir,
        config=ScanConfig(exclude=[], min_duplicate_lines=5, twin_similarity=0.6),
    )


def test_scan_package_reports_broken_symlink_as_unreadable_file(tmp_path: Path):
    # Regression: one unreadable file used to raise out of scan_package and
    # take the whole run down with a traceback instead of a finding.
    pkg_dir = tmp_path / "pkg"
    pkg_dir.mkdir()
    (pkg_dir / "ok.py").write_text("X = 1\n")
    (pkg_dir / "dangling.py").symlink_to(tmp_path / "does-not-exist.py")
    result = _scan_one(pkg_dir)
    assert result.files_scanned == 2
    unreadable = [i for i in result.issues if i.rule == "unreadable-file"]
    assert [i.file.name for i in unreadable] == ["dangling.py"]
    assert unreadable[0].severity == "error"
    assert unreadable[0].line == 1


@pytest.mark.skipif(
    sys.platform == "win32" or os.geteuid() == 0,
    reason="chmod 000 is not enforced for root or on Windows",
)
def test_scan_package_reports_permission_denied_as_unreadable_file(tmp_path: Path):
    pkg_dir = tmp_path / "pkg"
    pkg_dir.mkdir()
    secret = pkg_dir / "secret.py"
    secret.write_text("X = 1\n")
    secret.chmod(0o000)
    try:
        result = _scan_one(pkg_dir)
    finally:
        secret.chmod(0o644)
    assert result.files_scanned == 1
    assert [i.rule for i in result.issues] == ["unreadable-file"]
    assert "Permission denied" in result.issues[0].message


# ── JSON report: schema v1, locations, fingerprints ─────────────────────────


def test_issue_locations_are_one_based_character_columns(tmp_path: Path):
    pkg_dir = tmp_path / "pkg"
    pkg_dir.mkdir()
    # Non-ASCII before the flagged node: a byte column would be off by two.
    (pkg_dir / "m.py").write_text("é = 1\ndef f(x):\n    return x\n", encoding="utf-8")
    result = ds.scan_package(
        "pkg", pkg_dir, config=ScanConfig(exclude=[], min_duplicate_lines=5, twin_similarity=0.6)
    )
    by_rule = {i.rule: i for i in result.issues}
    issue = by_rule["missing-return-type"]
    assert (issue.line, issue.column) == (2, 1)
    assert (issue.end_line, issue.end_column) == (3, 13)


def test_fingerprints_are_stable_across_line_shifts_and_distinct_per_occurrence(
    tmp_path: Path,
):
    cfg = ScanConfig(exclude=[], min_duplicate_lines=5, twin_similarity=0.6)
    pkg_dir = tmp_path / "pkg"
    pkg_dir.mkdir()
    body = "def f(x):\n    return x\n\ndef g(x):\n    return x\n"
    (pkg_dir / "m.py").write_text(body)
    first = {i.message: i.fingerprint for i in ds.scan_package("pkg", pkg_dir, config=cfg).issues}

    (pkg_dir / "m.py").write_text("# shifted down\n\n" + body)
    second = {i.message: i.fingerprint for i in ds.scan_package("pkg", pkg_dir, config=cfg).issues}

    assert first == second
    assert len(set(first.values())) == len(first)
    assert all(len(fp) == 16 for fp in first.values())


def test_chunked_parallel_scan_matches_the_sequential_scan(tmp_path: Path, monkeypatch):
    """Large packages are scanned as file chunks across the pool and reassembled
    in the parent; the result must be byte-for-byte what scan_package gives."""
    root = tmp_path / "big"
    (root / "sub").mkdir(parents=True)
    body = (
        "import os\n"
        "class Base:\n"
        "    def run(self, x):\n"
        "        if x:\n"
        "            return os.getcwd()\n"
        "        if x is None:\n"
        "            raise ValueError(x)\n"
        "        for part in os.listdir('.'):\n"
        "            if part.startswith('.'):\n"
        "                continue\n"
        "        return None\n"
        "def helper(a, b):\n"
        "    return a if a else b\n"
    )
    for i in range(10):
        (root / f"m{i}.py").write_text(body.replace("Base", f"Base{i}"))
        (root / "sub" / f"n{i}.py").write_text(body.replace("helper", f"helper{i}"))
    (root / "broken.py").write_text("def (:\n")
    (root / "huge.py").write_text("x = 1\n" * 400)
    monkeypatch.setattr(ds, "PACKAGES", {"big": root})
    monkeypatch.setattr(ds, "PARALLEL_MIN_FILES", 4)
    monkeypatch.setattr(_cfg, "MAX_FILE_BYTES", 2000)
    config = ScanConfig(exclude=[], min_duplicate_lines=5, twin_similarity=0.6)
    sequential = ds.scan_package("big", root, config=config)
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
        parallel = asyncio.run(
            ds.review_packages_concurrently(["big"], config=config, executor=pool)
        )["big"]
    assert parallel.issues == sequential.issues
    assert parallel.files_scanned == sequential.files_scanned == 22
    assert parallel.total_lines == sequential.total_lines
    assert parallel.functions_scanned == sequential.functions_scanned
    assert parallel.file_mi == sequential.file_mi
    assert parallel.suppressed == sequential.suppressed
    assert {i.rule for i in parallel.issues} >= {
        "syntax-error",
        "scan-limit",
        "duplicate-function-body",
    }
