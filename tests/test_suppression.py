"""Tests for inline `# spaghetti-ignore` suppression (spaghetti.suppression)."""

from __future__ import annotations

from pathlib import Path

from spaghetti import detector as ds
from spaghetti.models import ScanConfig, ScanResult

# ── Inline suppression (# spaghetti-ignore) ──────────────────────────────────


def _scan_single_file(tmp_path: Path, source: str) -> ScanResult:
    pkg_dir = tmp_path / "suppress_pkg"
    pkg_dir.mkdir()
    (pkg_dir / "mod.py").write_text(source)
    return ds.scan_package(
        "suppress_pkg",
        pkg_dir,
        config=ScanConfig(exclude=[], min_duplicate_lines=5, twin_similarity=0.6),
    )


def test_suppression_marker_on_flagged_line(tmp_path: Path):
    result = _scan_single_file(
        tmp_path,
        "def f():\n"
        "    try:\n"
        "        pass\n"
        "    except:  # spaghetti-ignore[bare-except]: intentional catch-all\n"
        "        pass\n",
    )
    assert not any(i.rule == "bare-except" for i in result.issues)
    assert result.suppressed == 1


def test_suppression_marker_on_line_above(tmp_path: Path):
    result = _scan_single_file(
        tmp_path,
        "def f():\n"
        "    try:\n"
        "        pass\n"
        "    # spaghetti-ignore[bare-except]: intentional catch-all\n"
        "    except:\n"
        "        pass\n",
    )
    assert not any(i.rule == "bare-except" for i in result.issues)
    assert result.suppressed == 1


def test_suppression_named_rule_leaves_other_rules_on_same_line(tmp_path: Path):
    # A function with 4+ nested returns is flagged for excessive-returns at
    # its def line; suppressing a different rule there must not hide it.
    source = (
        "# spaghetti-ignore[long-function]: unrelated rule\n"
        "def f(x: int) -> int:\n"
        "    if x == 1:\n"
        "        if True:\n"
        "            return 1\n"
        "    if x == 2:\n"
        "        if True:\n"
        "            return 2\n"
        "    if x == 3:\n"
        "        if True:\n"
        "            return 3\n"
        "    if x == 4:\n"
        "        if True:\n"
        "            return 4\n"
        "    return 0\n"
    )
    result = _scan_single_file(tmp_path, source)
    assert any(i.rule == "excessive-returns" for i in result.issues)
    assert result.suppressed == 0


def test_suppression_bare_marker_suppresses_all_rules_on_line(tmp_path: Path):
    source = (
        "# spaghetti-ignore: reviewed, intentional\n"
        "def f(x: int) -> int:\n"
        "    if x == 1:\n"
        "        if True:\n"
        "            return 1\n"
        "    if x == 2:\n"
        "        if True:\n"
        "            return 2\n"
        "    if x == 3:\n"
        "        if True:\n"
        "            return 3\n"
        "    if x == 4:\n"
        "        if True:\n"
        "            return 4\n"
        "    return 0\n"
    )
    result = _scan_single_file(tmp_path, source)
    assert not any(i.rule == "excessive-returns" for i in result.issues)
    assert result.suppressed >= 1


def test_unsuppressed_issues_unaffected_elsewhere_in_file(tmp_path: Path):
    source = (
        "def suppressed():\n"
        "    try:\n"
        "        pass\n"
        "    except:  # spaghetti-ignore[bare-except]: reviewed\n"
        "        pass\n"
        "\n"
        "\n"
        "def not_suppressed():\n"
        "    try:\n"
        "        pass\n"
        "    except:\n"
        "        pass\n"
    )
    result = _scan_single_file(tmp_path, source)
    bare_excepts = [i for i in result.issues if i.rule == "bare-except"]
    assert len(bare_excepts) == 1
    assert result.suppressed == 1
    # Suppressed issues must not count toward severity totals / exit codes.
    assert result.warning_count == len([i for i in result.issues if i.severity == "warning"])


# ── scan_package end-to-end: suppression with cross-file rules ───────────────


def test_suppression_suppresses_sync_async_twin(tmp_path: Path):
    pkg_dir = tmp_path / "suppress_twin_pkg"
    pkg_dir.mkdir()
    body = (
        "    request = build_request()\n"
        "    result = execute(request)\n"
        "    return post_process(result)\n"
    ) * 3
    (pkg_dir / "a.py").write_text(
        f"# spaghetti-ignore[sync-async-duplication]: known tech-debt\n"
        f"def load() -> object:\n{body}"
        f"async def aload() -> object:\n{body}"
    )
    result = ds.scan_package(
        "suppress_twin_pkg",
        pkg_dir,
        config=ScanConfig(exclude=[], min_duplicate_lines=5, twin_similarity=0.6),
    )
    assert not any(i.rule == "sync-async-duplication" for i in result.issues)
    assert result.suppressed == 1
