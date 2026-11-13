"""Tests for the cross-file checks: package-level (spaghetti.checks.package_level) and
the single-package behaviour of the workspace-level graph checks."""

from __future__ import annotations

import ast
from pathlib import Path

from spaghetti import detector as ds
from spaghetti.checks.package_level import check_sync_async_twins_pkg
from spaghetti.checks.workspace_level import (
    build_import_graph,
    check_import_cycles_ws,
    check_module_coupling_ws,
    collect_raw_imports,
)
from spaghetti.config import MAX_MODULE_FAN_IN

# ── Cross-File: Import Cycles and Module Coupling ───────────────────────────
# These run over the whole workspace since Phase 2; see tests/test_workspace.py
# for the multi-package cases. The single-package behaviour is pinned here.


def _files_for_pkg(pkg_dir: Path) -> list[tuple[Path, ast.Module]]:
    files = []
    for py_file in sorted(pkg_dir.rglob("*.py")):
        source = py_file.read_text()
        tree = ast.parse(source, filename=str(py_file))
        files.append((py_file, tree))
    return files


def _graph_for(pkg_name: str, pkg_dir: Path, monkeypatch):
    monkeypatch.setattr(ds, "PACKAGES", {pkg_name: pkg_dir})
    raw = {fp: collect_raw_imports(tree) for fp, tree in _files_for_pkg(pkg_dir)}
    return build_import_graph({pkg_name: raw})


def test_import_cycle_detected(tmp_path: Path, monkeypatch):
    pkg_dir = tmp_path / "cycle_pkg"
    pkg_dir.mkdir()
    (pkg_dir / "__init__.py").write_text("")
    (pkg_dir / "a.py").write_text("from .b import something\n")
    (pkg_dir / "b.py").write_text("from .a import something_else\n")
    issues = check_import_cycles_ws(*_graph_for("cycle_pkg", pkg_dir, monkeypatch))
    assert len(issues) >= 1
    assert issues[0].rule == "import-cycle"
    assert issues[0].severity == "error"
    assert issues[0].package == "cycle_pkg"


def test_import_cycle_clean(tmp_path: Path, monkeypatch):
    pkg_dir = tmp_path / "clean_pkg"
    pkg_dir.mkdir()
    (pkg_dir / "__init__.py").write_text("")
    (pkg_dir / "a.py").write_text("x = 1\n")
    (pkg_dir / "b.py").write_text("from .a import x\n")
    assert check_import_cycles_ws(*_graph_for("clean_pkg", pkg_dir, monkeypatch)) == []


def test_module_coupling_flags_hub_module(tmp_path: Path, monkeypatch):
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
    issues = check_module_coupling_ws(*_graph_for("hub_pkg", pkg_dir, monkeypatch))
    assert len(issues) == 1
    assert issues[0].rule == "high-coupling"
    assert "hub_pkg.hub" in issues[0].message


def test_module_coupling_allows_high_fan_in_alone(tmp_path: Path, monkeypatch):
    # Fan-in alone (a legitimately central util everyone imports) must not
    # trigger — only both fan-in AND fan-out high does.
    pkg_dir = tmp_path / "util_pkg"
    pkg_dir.mkdir()
    (pkg_dir / "__init__.py").write_text("")
    (pkg_dir / "util.py").write_text("x = 1\n")
    for i in range(MAX_MODULE_FAN_IN + 1):
        (pkg_dir / f"user{i}.py").write_text("from .util import x\n")
    assert check_module_coupling_ws(*_graph_for("util_pkg", pkg_dir, monkeypatch)) == []


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


def test_sync_async_twins_reported_once_per_class(tmp_path: Path):
    body = (
        "class Request:\n"
        "    def read(self):\n"
        "        a = self.stream.read()\n"
        "        b = self.decode(a)\n"
        "        return b\n"
        "    async def aread(self):\n"
        "        a = await self.stream.aread()\n"
        "        b = self.decode(a)\n"
        "        return b\n"
        "class Response:\n"
        "    def read(self):\n"
        "        a = self.stream.read()\n"
        "        b = self.decode(a)\n"
        "        return b\n"
        "    async def aread(self):\n"
        "        a = await self.stream.aread()\n"
        "        b = self.decode(a)\n"
        "        return b\n"
    )
    path = tmp_path / "m.py"
    path.write_text(body)
    tree = ast.parse(body)
    issues = check_sync_async_twins_pkg("pkg", [(path, tree)], 0.6)
    assert [i.line for i in issues if i.rule == "sync-async-duplication"] == [2, 11]


def test_twin_pairs_are_reported_in_a_seed_independent_order(tmp_path: Path):
    body = "".join(
        f"{'async ' if name != 'get' else ''}def {name}(self):\n"
        "    a = self.s.read()\n    b = self.d(a)\n    c = self.e(b)\n    return c\n"
        for name in ("get", "aget", "get_async")
    )
    path = tmp_path / "m.py"
    path.write_text(body)
    issues = check_sync_async_twins_pkg("pkg", [(path, ast.parse(body))], 0.6)
    assert [i.message.split(" are ")[0] for i in issues] == [
        "get() and aget()",
        "get() and get_async()",
    ]
