"""Tests for the cross-file (package-level) checks in spaghetti.checks.package_level."""

from __future__ import annotations

import ast
from pathlib import Path

from spaghetti import detector as ds
from spaghetti.checks.package_level import check_import_cycles_pkg, check_module_coupling_pkg
from spaghetti.config import MAX_MODULE_FAN_IN

# ── Cross-File: Import Cycles ───────────────────────────────────────────────


def test_check_import_cycles_pkg_detects_cycle(tmp_path: Path):
    pkg_dir = tmp_path / "cycle_pkg"
    pkg_dir.mkdir()
    (pkg_dir / "__init__.py").write_text("")
    (pkg_dir / "a.py").write_text("from .b import something\n")
    (pkg_dir / "b.py").write_text("from .a import something_else\n")
    pkg_name = "cycle_pkg"

    files = []
    for py_file in sorted(pkg_dir.rglob("*.py")):
        source = py_file.read_text()
        tree = ast.parse(source, filename=str(py_file))
        files.append((py_file, tree))

    original_packages = ds.PACKAGES
    ds.PACKAGES = {pkg_name: pkg_dir}
    try:
        issues = check_import_cycles_pkg(pkg_name, files)
        assert len(issues) >= 1
        assert issues[0].rule == "import-cycle"
        assert issues[0].severity == "error"
    finally:
        ds.PACKAGES = original_packages


def test_check_import_cycles_pkg_clean(tmp_path: Path):
    pkg_dir = tmp_path / "clean_pkg"
    pkg_dir.mkdir()
    (pkg_dir / "__init__.py").write_text("")
    (pkg_dir / "a.py").write_text("x = 1\n")
    (pkg_dir / "b.py").write_text("from .a import x\n")
    pkg_name = "clean_pkg"

    files = []
    for py_file in sorted(pkg_dir.rglob("*.py")):
        source = py_file.read_text()
        tree = ast.parse(source, filename=str(py_file))
        files.append((py_file, tree))

    original_packages = ds.PACKAGES
    ds.PACKAGES = {pkg_name: pkg_dir}
    try:
        issues = check_import_cycles_pkg(pkg_name, files)
        assert issues == []
    finally:
        ds.PACKAGES = original_packages


# ── Cross-File: Module Coupling ─────────────────────────────────────────────


def _files_for_pkg(pkg_dir: Path) -> list[tuple[Path, ast.Module]]:
    files = []
    for py_file in sorted(pkg_dir.rglob("*.py")):
        source = py_file.read_text()
        tree = ast.parse(source, filename=str(py_file))
        files.append((py_file, tree))
    return files


def test_check_module_coupling_pkg_flags_hub_module(tmp_path: Path):
    pkg_dir = tmp_path / "hub_pkg"
    pkg_dir.mkdir()
    (pkg_dir / "__init__.py").write_text("")

    n = MAX_MODULE_FAN_IN + 1  # also > MAX_MODULE_FAN_OUT since both are equal
    for i in range(n):
        (pkg_dir / f"dep{i}.py").write_text("x = 1\n")
    (pkg_dir / "hub.py").write_text(
        "\n".join(f"from .dep{i} import x as x{i}" for i in range(n)) + "\n"
    )
    for i in range(n):
        (pkg_dir / f"user{i}.py").write_text("from .hub import hub\n")

    pkg_name = "hub_pkg"
    files = _files_for_pkg(pkg_dir)

    original_packages = ds.PACKAGES
    ds.PACKAGES = {pkg_name: pkg_dir}
    try:
        issues = check_module_coupling_pkg(pkg_name, files)
        assert len(issues) == 1
        assert issues[0].rule == "high-coupling"
        assert f"{pkg_name}.hub" in issues[0].message
    finally:
        ds.PACKAGES = original_packages


def test_check_module_coupling_pkg_allows_high_fan_in_alone(tmp_path: Path):
    # Sanity check: fan-in alone (a legitimately central util everyone
    # imports) must not trigger — only both fan-in AND fan-out high does.
    pkg_dir = tmp_path / "util_pkg"
    pkg_dir.mkdir()
    (pkg_dir / "__init__.py").write_text("")
    (pkg_dir / "util.py").write_text("x = 1\n")
    n = MAX_MODULE_FAN_IN + 1
    for i in range(n):
        (pkg_dir / f"user{i}.py").write_text("from .util import x\n")

    pkg_name = "util_pkg"
    files = _files_for_pkg(pkg_dir)

    original_packages = ds.PACKAGES
    ds.PACKAGES = {pkg_name: pkg_dir}
    try:
        issues = check_module_coupling_pkg(pkg_name, files)
        assert issues == []
    finally:
        ds.PACKAGES = original_packages


def test_check_module_coupling_pkg_clean(tmp_path: Path):
    pkg_dir = tmp_path / "clean_pkg"
    pkg_dir.mkdir()
    (pkg_dir / "__init__.py").write_text("")
    (pkg_dir / "a.py").write_text("x = 1\n")
    (pkg_dir / "b.py").write_text("from .a import x\n")

    pkg_name = "clean_pkg"
    files = _files_for_pkg(pkg_dir)

    original_packages = ds.PACKAGES
    ds.PACKAGES = {pkg_name: pkg_dir}
    try:
        issues = check_module_coupling_pkg(pkg_name, files)
        assert issues == []
    finally:
        ds.PACKAGES = original_packages


# ── Cross-File: Duplicate Function Bodies ────────────────────────────────────


def test_check_duplicate_functions_pkg_detects_duplicate(tmp_path: Path):
    pkg_dir = tmp_path / "dup_pkg"
    pkg_dir.mkdir()
    body = "    result = x + y\n    return result\n" * 3
    (pkg_dir / "a.py").write_text(f"def func_a(x: int, y: int) -> int:\n{body}")
    (pkg_dir / "b.py").write_text(f"def func_b(x: int, y: int) -> int:\n{body}")
    pkg_name = "dup_pkg"

    files = []
    for py_file in sorted(pkg_dir.rglob("*.py")):
        source = py_file.read_text()
        tree = ast.parse(source, filename=str(py_file))
        files.append((py_file, tree))

    issues = ds.check_duplicate_functions_pkg(pkg_name, files, min_lines=5)
    assert len(issues) == 1
    assert issues[0].rule == "duplicate-function-body"


def test_check_duplicate_functions_pkg_skips_trivial(tmp_path: Path):
    pkg_dir = tmp_path / "trivial_pkg"
    pkg_dir.mkdir()
    (pkg_dir / "a.py").write_text("def func_a():\n    pass\n")
    (pkg_dir / "b.py").write_text("def func_b():\n    pass\n")
    pkg_name = "trivial_pkg"

    files = []
    for py_file in sorted(pkg_dir.rglob("*.py")):
        source = py_file.read_text()
        tree = ast.parse(source, filename=str(py_file))
        files.append((py_file, tree))

    issues = ds.check_duplicate_functions_pkg(pkg_name, files, min_lines=5)
    assert issues == []


# ── Cross-File: Sync/Async Twin Duplication ─────────────────────────────────


def test_check_sync_async_twins_detects_pair(tmp_path: Path):
    pkg_dir = tmp_path / "twin_pkg"
    pkg_dir.mkdir()
    body = (
        "    request = build_request()\n"
        "    result = execute(request)\n"
        "    return post_process(result)\n"
    ) * 3
    (pkg_dir / "a.py").write_text(
        f"def load() -> object:\n{body}async def aload() -> object:\n{body}"
    )
    pkg_name = "twin_pkg"

    files = []
    for py_file in sorted(pkg_dir.rglob("*.py")):
        source = py_file.read_text()
        tree = ast.parse(source, filename=str(py_file))
        files.append((py_file, tree))

    issues = ds.check_sync_async_twins_pkg(pkg_name, files, min_ratio=0.6)
    assert len(issues) == 1
    assert issues[0].rule == "sync-async-duplication"


def test_check_sync_async_twins_skips_different_bodies(tmp_path: Path):
    pkg_dir = tmp_path / "diff_pkg"
    pkg_dir.mkdir()
    (pkg_dir / "a.py").write_text(
        "def load() -> int:\n"
        "    return 1\n"
        "async def aload() -> int:\n"
        "    return await fetch_async()\n"
    )
    pkg_name = "diff_pkg"

    files = []
    for py_file in sorted(pkg_dir.rglob("*.py")):
        source = py_file.read_text()
        tree = ast.parse(source, filename=str(py_file))
        files.append((py_file, tree))

    issues = ds.check_sync_async_twins_pkg(pkg_name, files, min_ratio=0.6)
    assert issues == []


def test_check_sync_async_twins_skips_short_functions(tmp_path: Path):
    pkg_dir = tmp_path / "short_pkg"
    pkg_dir.mkdir()
    (pkg_dir / "a.py").write_text("def f():\n    pass\nasync def af():\n    pass\n")
    pkg_name = "short_pkg"

    files = []
    for py_file in sorted(pkg_dir.rglob("*.py")):
        source = py_file.read_text()
        tree = ast.parse(source, filename=str(py_file))
        files.append((py_file, tree))

    issues = ds.check_sync_async_twins_pkg(pkg_name, files, min_ratio=0.6)
    assert issues == []
