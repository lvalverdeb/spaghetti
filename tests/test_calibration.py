"""Behaviour fixed or narrowed by the 2026-11 precision audit (docs/precision.md)."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

import spaghetti.config as _cfg
from spaghetti import detector as ds
from spaghetti.checks.ast_per_file import (
    check_boolean_flag_params,
    check_dead_code,
    check_deep_inheritance,
    check_encapsulation_violations,
    check_star_imports,
    check_unused_imports,
)
from spaghetti.checks.package_level import (
    _build_import_graph,
    check_duplicate_functions_pkg,
    check_import_cycles_pkg,
)
from spaghetti.models import Issue, ScanConfig
from spaghetti.rules import DEFAULT_RULE_IDS, RULE_CATALOG


def _parse(source: str) -> ast.Module:
    return ast.parse(source)


# ── unused-import ────────────────────────────────────────────────────────────


def test_unused_import_sees_string_annotations_and_type_checking_blocks():
    source = (
        "from typing import TYPE_CHECKING\n"
        "if TYPE_CHECKING:\n"
        "    from .console import Console\n"
        "    from .style import Style\n"
        "def f(c: 'Console') -> 'list[Style]':\n"
        "    return []\n"
    )
    assert check_unused_imports(_parse(source), Path("f.py"), "pkg") == []


def test_unused_import_honours_explicit_reexport_alias():
    source = "from .models import Foo as Foo\nimport os as os\n"
    assert check_unused_imports(_parse(source), Path("f.py"), "pkg") == []


def test_unused_import_still_flags_a_genuinely_unused_name():
    source = "import os\nimport sys\nprint(sys.argv)\n"
    issues = check_unused_imports(_parse(source), Path("f.py"), "pkg")
    assert [i.message for i in issues] == ["'os' imported but never used"]


# ── import-cycle ─────────────────────────────────────────────────────────────


def _pkg(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, files: dict[str, str]
) -> tuple[str, list[tuple[Path, ast.Module]]]:
    pkg_dir = tmp_path / "pkg"
    pkg_dir.mkdir()
    parsed = []
    for name, src in files.items():
        p = pkg_dir / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(src)
        parsed.append((p, ast.parse(src)))
    monkeypatch.setattr(ds, "PACKAGES", {"pkg": pkg_dir})
    return "pkg", sorted(parsed, key=lambda t: t[0])


def test_main_guard_imports_do_not_create_cycles(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    name, files = _pkg(
        tmp_path,
        monkeypatch,
        {
            "__init__.py": "",
            "console.py": "from .pager import Pager\n",
            "pager.py": (
                'class Pager: ...\nif __name__ == "__main__":\n    from .console import Console\n'
            ),
        },
    )
    assert check_import_cycles_pkg(name, files) == []


def test_init_reexport_of_submodules_is_not_a_cycle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    # pkg/__init__ imports its submodules; a submodule does ``from pkg import
    # other`` to reach a *sibling module*. That binds pkg.other, not pkg.
    name, files = _pkg(
        tmp_path,
        monkeypatch,
        {
            "__init__.py": "from .a import A\nfrom .b import B\n",
            "a.py": "from pkg import b\nclass A: ...\n",
            "b.py": "class B: ...\n",
        },
    )
    graph, _ = _build_import_graph(name, files)
    assert graph["pkg.a"] == {"pkg.b"}
    assert check_import_cycles_pkg(name, files) == []


def test_real_cycle_is_still_reported(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    name, files = _pkg(
        tmp_path,
        monkeypatch,
        {"__init__.py": "", "a.py": "from .b import B\n", "b.py": "from .a import A\n"},
    )
    assert [i.rule for i in check_import_cycles_pkg(name, files)] == ["import-cycle"]


# ── narrowed rules ───────────────────────────────────────────────────────────


def test_boolean_flags_count_only_positional_booleans():
    source = "def f(a, *, verbose=False, dry_run=False, force=False):\n    pass\n"
    assert check_boolean_flag_params(_parse(source), Path("f.py"), "pkg") == []
    source = "def f(a, verbose=False, dry_run=False, force=False):\n    pass\n"
    assert len(check_boolean_flag_params(_parse(source), Path("f.py"), "pkg")) == 1


def test_dead_code_allows_yield_after_raise_generator_idiom():
    source = "def gen():\n    raise StopIteration\n    yield\n"
    assert check_dead_code(_parse(source), Path("f.py"), "pkg") == []
    source = "def f():\n    return 1\n    print('never')\n"
    assert len(check_dead_code(_parse(source), Path("f.py"), "pkg")) == 1


def test_deep_inheritance_skips_exception_hierarchies():
    source = (
        "class A(Exception): ...\nclass B(A): ...\nclass C(B): ...\n"
        "class D(C): ...\nclass E(D): ...\n"
    )
    assert check_deep_inheritance(_parse(source), Path("f.py"), "pkg") == []


def test_encapsulation_allows_same_class_private_access():
    source = (
        "class P:\n"
        "    def __init__(self):\n        self._x = 1\n"
        "    def __eq__(self, other):\n        return self._x == other._x\n"
        "    def leak(self, stranger):\n        return stranger._secret\n"
    )
    issues = check_encapsulation_violations(_parse(source), Path("f.py"), "pkg")
    assert [i.message for i in issues] == [
        "Accesses private member '._secret' through something other than self/cls"
    ]


def test_star_import_skips_package_init():
    source = "from .models import *\n"
    assert check_star_imports(_parse(source), Path("__init__.py"), "pkg") == []
    assert len(check_star_imports(_parse(source), Path("m.py"), "pkg")) == 1


def test_duplicate_function_body_skips_dunder_protocol_hooks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    body = "    def __repr__(self):\n        a = 1\n        b = 2\n        c = 3\n        return f'{a}{b}{c}'\n"
    name, files = _pkg(
        tmp_path, monkeypatch, {"a.py": "class A:\n" + body, "b.py": "class B:\n" + body}
    )
    assert check_duplicate_functions_pkg(name, files, 5) == []


# ── enabled_by_default plumbing ──────────────────────────────────────────────


@pytest.fixture
def restore_overrides(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(_cfg, "RULE_OVERRIDES", {})


def _scan(tmp_path: Path, source: str):
    pkg_dir = tmp_path / "pkg"
    pkg_dir.mkdir(exist_ok=True)
    (pkg_dir / "m.py").write_text(source)
    cfg = ScanConfig(exclude=[], min_duplicate_lines=5, twin_similarity=0.6)
    return ds.scan_package("pkg", pkg_dir, config=cfg)


def test_rules_off_by_default_are_dropped_unless_opted_in(
    tmp_path: Path, restore_overrides, monkeypatch: pytest.MonkeyPatch
):
    source = "def f() -> int:\n    x = 42\n    if x:\n        y = 1\n        z = 2\n    return x\n"
    rules = {i.rule for i in _scan(tmp_path, source).issues}
    assert "magic-number" not in rules and "missing-else" not in rules
    monkeypatch.setattr(_cfg, "RULE_OVERRIDES", {"magic-number": "warning"})
    issues = _scan(tmp_path, source).issues
    magic = [i for i in issues if i.rule == "magic-number"]
    assert magic and all(i.severity == "warning" for i in magic)


def test_catalog_marks_the_audited_rules_off():
    off = set(RULE_CATALOG) - DEFAULT_RULE_IDS
    assert off == {
        "low-cohesion",
        "missing-else",
        "pass-through-method",
        "magic-string",
        "magic-number",
        "star-import",
        "unused-import",
        "potential-circular-import",
    }


def test_apply_rule_overrides_keeps_unknown_rules():
    # A rule not in the catalog (e.g. a future experimental one) is never dropped silently.
    issue = Issue(file=Path("f.py"), line=1, severity="info", rule="future-rule", message="m")
    assert ds._apply_rule_overrides([issue]) == [issue]
