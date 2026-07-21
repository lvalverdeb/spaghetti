"""
Tests for spaghetti.detector.

spaghetti is an installed workspace package, so detector is imported
normally here rather than via a sys.path insertion. Run with:

    uv run pytest spaghetti/tests/test_detector.py

Covers:
  - A representative sample of the AST/source-level check functions
  - compute_score()
  - scan_package() end-to-end against a small synthetic package on disk
  - The concurrent Agent-based review orchestration (SpaghettiReviewAgent,
    review_packages_concurrently): correctness, genuine concurrency (proven
    deterministically via a threading.Barrier rather than wall-clock timing),
    the close-barrier/_assert_open() guard, and exception propagation when
    one package's scan fails.
"""

from __future__ import annotations

import ast
import asyncio
import concurrent.futures
import threading
from pathlib import Path

import pytest

from spaghetti import detector as ds
from spaghetti.checks.ast_per_file import (
    check_bare_except,
    check_boolean_flag_params,
    check_circular_imports,
    check_complexity,
    check_dead_code,
    check_deep_inheritance,
    check_deep_nesting,
    check_duplicate_branches,
    check_encapsulation_violations,
    check_excessive_decorators,
    check_excessive_params,
    check_excessive_returns,
    check_global_mutations,
    check_god_class,
    check_god_module,
    check_layer_violations,
    check_lazy_class,
    check_long_functions,
    check_low_cohesion,
    check_magic_numbers,
    check_magic_strings,
    check_message_chains,
    check_missing_else,
    check_missing_types,
    check_mutable_defaults,
    check_pass_through_methods,
    check_scope_mutations,
    check_star_imports,
    check_swallowed_exceptions,
    check_transport_in_library,
    check_untyped_dicts,
    check_unused_imports,
)
from spaghetti.checks.package_level import check_import_cycles_pkg, check_module_coupling_pkg
from spaghetti.checks.text_per_file import check_long_file, check_todo_markers
from spaghetti.cli import (
    _load_packages_from_config,
    _parse_package_args,
    discover_cwd_packages,
    resolve_packages,
)
from spaghetti.config import (
    MAX_CLASS_METHODS,
    MAX_CLASS_WMC,
    MAX_FILE_LINES,
    MAX_FUNC_PARAMS,
    MAX_FUNCTION_LINES,
    MAX_MODULE_FAN_IN,
    MAX_NESTING_DEPTH,
    MIN_METHODS_FOR_COHESION,
)
from spaghetti.models import Issue, ScanConfig, ScanResult
from spaghetti.scoring import (
    build_remediation_plan,
    compute_priority_score,
    compute_score,
    plan_report,
)


def _parse(source: str) -> ast.Module:
    return ast.parse(source)


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


# ── Representative check functions ──────────────────────────────────────────


def test_check_long_functions_flags_over_threshold():
    body = "\n".join(f"    x{i} = {i}" for i in range(MAX_FUNCTION_LINES + 5))
    source = f"def long_func():\n{body}\n    return x0\n"
    issues = check_long_functions(_parse(source), Path("f.py"), "pkg")
    assert len(issues) == 1
    assert issues[0].rule == "long-function"
    assert issues[0].severity == "warning"
    assert "long_func" in issues[0].message


def test_check_long_functions_ignores_short_function():
    source = "def short_func():\n    return 1\n"
    issues = check_long_functions(_parse(source), Path("f.py"), "pkg")
    assert issues == []


def test_check_deep_nesting_flags_excessive_nesting():
    nested = "if True:\n"
    indent = "    "
    for i in range(MAX_NESTING_DEPTH + 2):
        nested += indent * (i + 1) + "if True:\n"
    nested += indent * (MAX_NESTING_DEPTH + 3) + "pass\n"
    source = "def deeply_nested():\n" + "\n".join("    " + line for line in nested.splitlines())
    issues = check_deep_nesting(_parse(source), Path("f.py"), "pkg")
    assert len(issues) == 1
    assert issues[0].rule == "deep-nesting"


def test_check_bare_except_flags_bare_except():
    source = "def f():\n    try:\n        pass\n    except:\n        pass\n"
    issues = check_bare_except(_parse(source), Path("f.py"), "pkg")
    assert len(issues) == 1
    assert issues[0].rule == "bare-except"


def test_check_bare_except_allows_typed_except():
    source = "def f():\n    try:\n        pass\n    except ValueError:\n        pass\n"
    issues = check_bare_except(_parse(source), Path("f.py"), "pkg")
    assert issues == []


def test_check_long_file_flags_over_threshold():
    source = "\n".join(f"x = {i}" for i in range(MAX_FILE_LINES + 10))
    issues = check_long_file(source, Path("f.py"), "pkg")
    assert len(issues) == 1
    assert issues[0].rule == "long-file"


def test_check_todo_markers_flags_todo_comment():
    source = "x = 1  # TODO: fix this later\n"
    issues = check_todo_markers(source, Path("f.py"), "pkg")
    assert len(issues) == 1
    assert issues[0].rule == "todo-marker"


# ── compute_score() ──────────────────────────────────────────────────────────


def test_compute_score_perfect_for_empty_result():
    score, grade = compute_score(ScanResult())
    assert score == 100.0
    assert grade == "A"


def test_compute_score_degrades_with_errors():
    result = ScanResult(
        issues=[
            Issue(file=Path("f.py"), line=1, severity="error", rule="x", message="m")
            for _ in range(5)
        ],
        total_lines=1000,
    )
    score, grade = compute_score(result)
    assert score < 100.0
    assert grade != "A"


# ── scan_package() end-to-end ────────────────────────────────────────────────


@pytest.fixture
def fake_package(tmp_path: Path) -> Path:
    pkg_dir = tmp_path / "fake_pkg"
    pkg_dir.mkdir()
    (pkg_dir / "clean.py").write_text("def add(a: int, b: int) -> int:\n    return a + b\n")
    (pkg_dir / "messy.py").write_text(
        "def f():\n    try:\n        pass\n    except:\n        pass\n"
    )
    return pkg_dir


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
    # A function with 4+ returns is flagged for excessive-returns at its def
    # line; suppressing a different rule there must not hide it.
    source = (
        "# spaghetti-ignore[long-function]: unrelated rule\n"
        "def f(x: int) -> int:\n"
        "    if x == 1:\n"
        "        return 1\n"
        "    if x == 2:\n"
        "        return 2\n"
        "    if x == 3:\n"
        "        return 3\n"
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
        "        return 1\n"
        "    if x == 2:\n"
        "        return 2\n"
        "    if x == 3:\n"
        "        return 3\n"
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


# ── SpaghettiReviewAgent ──────────────────────────────────────────────────────


def test_agent_review_populates_result_and_calls_scan_package(
    monkeypatch: pytest.MonkeyPatch, fake_package: Path
):
    captured: dict[str, object] = {}

    def fake_scan_package(pkg_name, pkg_path, *, config):
        captured.update(pkg_name=pkg_name, pkg_path=pkg_path, config=config)
        return ScanResult(files_scanned=1)

    monkeypatch.setattr(ds, "scan_package", fake_scan_package)

    async def run() -> ScanResult:
        # Use ThreadPoolExecutor: ProcessPoolExecutor would fail because
        # fake_scan_package is a local closure that cannot be pickled.
        executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
        try:
            agent = ds.SpaghettiReviewAgent(
                "fake_pkg",
                fake_package,
                config=ScanConfig(exclude=["x"], min_duplicate_lines=7, twin_similarity=0.5),
                executor=executor,
                skip_logger=True,
            )
            async with agent:
                result = await agent.review()
            assert agent.result is result
            return result
        finally:
            executor.shutdown(wait=False)

    result = asyncio.run(run())
    assert result.files_scanned == 1
    assert captured == {
        "pkg_name": "fake_pkg",
        "pkg_path": fake_package,
        "config": ScanConfig(exclude=["x"], min_duplicate_lines=7, twin_similarity=0.5),
    }


def test_agent_review_after_close_raises():
    async def run() -> None:
        executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
        try:
            agent = ds.SpaghettiReviewAgent(
                "fake_pkg",
                Path("/nonexistent"),
                config=ScanConfig(exclude=[], min_duplicate_lines=5, twin_similarity=0.6),
                executor=executor,
                skip_logger=True,
            )
            agent.close()
            with pytest.raises(RuntimeError, match="is closed"):
                await agent.review()
        finally:
            executor.shutdown(wait=False)

    asyncio.run(run())


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


# ── Generic package registry: --config / --package / resolve_packages ──────


def test_load_packages_from_config_resolves_relative_to_config_dir(tmp_path: Path):
    config_dir = tmp_path / "conf"
    config_dir.mkdir()
    (config_dir / "spaghetti.yaml").write_text(
        "packages:\n  my-lib: ../my-lib/src/my_lib\n  other: /abs/other\n"
    )

    packages = _load_packages_from_config(config_dir / "spaghetti.yaml")

    assert packages == {
        "my-lib": (tmp_path / "my-lib" / "src" / "my_lib").resolve(),
        "other": Path("/abs/other"),
    }


def test_load_packages_from_config_missing_packages_key_errors(tmp_path: Path):
    config_path = tmp_path / "spaghetti.yaml"
    config_path.write_text("not_packages: {}\n")

    with pytest.raises(SystemExit, match="must define a top-level 'packages' mapping"):
        _load_packages_from_config(config_path)


def test_load_packages_from_config_bad_yaml_errors(tmp_path: Path):
    config_path = tmp_path / "spaghetti.yaml"
    config_path.write_text("packages: [this, is, a, list, not, a, mapping]\n")

    with pytest.raises(SystemExit, match="must define a top-level 'packages' mapping"):
        _load_packages_from_config(config_path)


def test_load_packages_from_config_missing_file_errors(tmp_path: Path):
    with pytest.raises(SystemExit, match="could not read --config"):
        _load_packages_from_config(tmp_path / "does-not-exist.yaml")


def test_parse_package_args_resolves_relative_to_cwd(tmp_path: Path):
    packages = _parse_package_args(["my-lib=src/my_lib"], cwd=tmp_path)
    assert packages == {"my-lib": (tmp_path / "src" / "my_lib").resolve()}


def test_parse_package_args_multiple_entries(tmp_path: Path):
    packages = _parse_package_args(["a=src/a", "b=src/b"], cwd=tmp_path)
    assert packages == {
        "a": (tmp_path / "src" / "a").resolve(),
        "b": (tmp_path / "src" / "b").resolve(),
    }


@pytest.mark.parametrize("bad_entry", ["no-equals-sign", "=missing-name", "name="])
def test_parse_package_args_rejects_malformed_entries(tmp_path: Path, bad_entry: str):
    with pytest.raises(SystemExit, match="expects NAME=PATH"):
        _parse_package_args([bad_entry], cwd=tmp_path)


def test_resolve_packages_defaults_when_no_config_or_package_args(tmp_path: Path):
    defaults = {"a": Path("/fake/a")}
    result = resolve_packages(config_path=None, package_args=[], defaults=defaults, cwd=tmp_path)
    assert result == defaults
    # Must be a copy, not the same object, so callers can't mutate defaults.
    assert result is not defaults


def test_resolve_packages_config_replaces_defaults_entirely(tmp_path: Path):
    config_path = tmp_path / "spaghetti.yaml"
    config_path.write_text("packages:\n  configured: src/configured\n")
    defaults = {"a": Path("/fake/a")}

    result = resolve_packages(
        config_path=config_path, package_args=[], defaults=defaults, cwd=tmp_path
    )

    assert "a" not in result
    assert result == {"configured": (tmp_path / "src" / "configured").resolve()}


def test_resolve_packages_package_args_overlay_config(tmp_path: Path):
    config_path = tmp_path / "spaghetti.yaml"
    config_path.write_text("packages:\n  configured: src/configured\n")

    result = resolve_packages(
        config_path=config_path,
        package_args=["extra=src/extra", "configured=src/override"],
        defaults={},
        cwd=tmp_path,
    )

    assert result == {
        "configured": (tmp_path / "src" / "override").resolve(),
        "extra": (tmp_path / "src" / "extra").resolve(),
    }


def test_resolve_packages_package_args_alone_ignore_defaults(tmp_path: Path):
    result = resolve_packages(
        config_path=None,
        package_args=["only=src/only"],
        defaults={"a": Path("/fake/a")},
        cwd=tmp_path,
    )
    assert result == {"only": (tmp_path / "src" / "only").resolve()}


# ── cwd auto-discovery (bare invocation: no --config, no --package) ─────────


def test_discover_cwd_packages_finds_subdirectories_with_python(tmp_path: Path):
    (tmp_path / "alpha").mkdir()
    (tmp_path / "alpha" / "mod.py").write_text("x = 1\n")
    (tmp_path / "beta").mkdir()
    (tmp_path / "beta" / "nested").mkdir()
    (tmp_path / "beta" / "nested" / "mod.py").write_text("x = 1\n")

    packages, loose_root_name = discover_cwd_packages(tmp_path)

    assert packages == {"alpha": tmp_path / "alpha", "beta": tmp_path / "beta"}
    assert loose_root_name is None


def test_discover_cwd_packages_skips_dirs_with_no_python(tmp_path: Path):
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "readme.txt").write_text("no python here\n")
    (tmp_path / "alpha").mkdir()
    (tmp_path / "alpha" / "mod.py").write_text("x = 1\n")

    packages, _ = discover_cwd_packages(tmp_path)

    assert packages == {"alpha": tmp_path / "alpha"}


def test_discover_cwd_packages_skips_noise_directories(tmp_path: Path):
    for noisy in (".venv", "__pycache__", "node_modules", ".git", "build", "dist", "foo.egg-info"):
        d = tmp_path / noisy
        d.mkdir()
        (d / "mod.py").write_text("x = 1\n")  # even with .py inside, must be skipped
    (tmp_path / "alpha").mkdir()
    (tmp_path / "alpha" / "mod.py").write_text("x = 1\n")

    packages, _ = discover_cwd_packages(tmp_path)

    assert packages == {"alpha": tmp_path / "alpha"}


def test_discover_cwd_packages_bundles_loose_root_files(tmp_path: Path):
    (tmp_path / "main.py").write_text("x = 1\n")
    (tmp_path / "alpha").mkdir()
    (tmp_path / "alpha" / "mod.py").write_text("x = 1\n")

    packages, loose_root_name = discover_cwd_packages(tmp_path)

    assert loose_root_name == tmp_path.name
    assert packages == {"alpha": tmp_path / "alpha", tmp_path.name: tmp_path}


def test_discover_cwd_packages_empty_cwd_returns_empty_registry(tmp_path: Path):
    packages, loose_root_name = discover_cwd_packages(tmp_path)
    assert packages == {}
    assert loose_root_name is None


def test_discover_cwd_packages_does_not_special_case_boti_names(tmp_path: Path):
    """No hardcoded skip list — a directory literally named 'boti' found
    under cwd is discovered like any other, since the guarantee is 'never
    silently default to the workspace registry', not 'never scan a
    directory that happens to be named boti'."""
    (tmp_path / "boti").mkdir()
    (tmp_path / "boti" / "mod.py").write_text("x = 1\n")

    packages, _ = discover_cwd_packages(tmp_path)

    assert packages == {"boti": tmp_path / "boti"}


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


def test_main_bare_invocation_discovers_cwd_and_never_scans_boti(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
):
    """End-to-end: `spaghetti` with zero flags, run from a tmp cwd containing
    its own subpackage plus a loose root script, must scan exactly that —
    never the workspace's real boti/boti-data/boti-dask registry."""
    (tmp_path / "mylib").mkdir()
    (tmp_path / "mylib" / "core.py").write_text(
        "def f():\n    try:\n        pass\n    except:\n        pass\n"
    )
    (tmp_path / "loose.py").write_text("x = 1\n")

    monkeypatch.setattr(ds, "PACKAGES", dict(ds.PACKAGES))  # restore after test
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("sys.argv", ["spaghetti", "--severity", "info"])

    exit_code = ds.main()

    assert set(ds.PACKAGES) == {"mylib", tmp_path.name}
    assert "boti" not in ds.PACKAGES
    assert "boti-data" not in ds.PACKAGES
    assert "boti-dask" not in ds.PACKAGES
    assert exit_code in (0, 1, 2)
    out = capsys.readouterr().out
    assert "mylib" in out
    assert "bare-except" in out


def test_main_bare_invocation_empty_cwd_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
):
    monkeypatch.setattr(ds, "PACKAGES", dict(ds.PACKAGES))  # restore after test
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("sys.argv", ["spaghetti"])

    with pytest.raises(SystemExit) as exc_info:
        ds.main()

    assert exc_info.value.code == 2
    assert "no packages to scan" in capsys.readouterr().err


def test_main_scans_ad_hoc_package_via_cli_flag(
    fake_package: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
):
    """End-to-end: --package points main() at an arbitrary directory outside
    the built-in DEFAULT_PACKAGES registry, proving the CLI plumbing (not
    just resolve_packages() in isolation) actually drives a real scan."""
    monkeypatch.setattr(ds, "PACKAGES", dict(ds.PACKAGES))  # restore after test
    monkeypatch.setattr(
        "sys.argv",
        ["spaghetti", "--package", f"fake_pkg={fake_package}", "--severity", "info"],
    )

    exit_code = ds.main()

    assert ds.PACKAGES == {"fake_pkg": fake_package.resolve()}
    assert exit_code in (0, 1, 2)
    out = capsys.readouterr().out
    assert "fake_pkg" in out
    assert "bare-except" in out


def test_main_errors_on_unknown_package_name(fake_package: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(ds, "PACKAGES", dict(ds.PACKAGES))  # restore after test
    monkeypatch.setattr(
        "sys.argv",
        [
            "spaghetti",
            "--package",
            f"fake_pkg={fake_package}",
            "--packages",
            "does-not-exist",
        ],
    )

    with pytest.raises(SystemExit):
        ds.main()


def test_main_errors_on_nonexistent_package_path(
    fake_package: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
):
    """Regression test: a --package NAME=PATH pointing at a path that doesn't
    exist (e.g. a typo, or a relative PATH resolved against the wrong cwd —
    the real-world case that surfaced this: `--package boti-dask=src/boti_dask`
    run from the workspace root instead of from inside boti-dask/) used to
    scan silently to an empty, "clean" 0-issues/grade-A result instead of
    erroring — indistinguishable from a genuinely clean package that actually
    got scanned.
    """
    missing_path = fake_package / "does-not-exist-on-disk"
    monkeypatch.setattr(ds, "PACKAGES", dict(ds.PACKAGES))  # restore after test
    monkeypatch.setattr(
        "sys.argv",
        ["spaghetti", "--package", f"fake_pkg={missing_path}", "--severity", "info"],
    )

    with pytest.raises(SystemExit) as exc_info:
        ds.main()

    assert exc_info.value.code == 2
    assert "package path(s) do not exist" in capsys.readouterr().err


# ── High Complexity ──────────────────────────────────────────────────────────


def test_check_complexity_flags_high_complexity():
    source = (
        "def complex_func(x):\n"
        "    if x > 0:\n"
        "        if x > 10:\n"
        "            if x > 20:\n"
        "                for i in range(x):\n"
        "                    if i % 2 == 0:\n"
        "                        while i > 0:\n"
        "                            try:\n"
        "                                if i == 5:\n"
        "                                    pass\n"
        "                                elif i == 3:\n"
        "                                    pass\n"
        "                                else:\n"
        "                                    pass\n"
        "                            except:\n"
        "                                pass\n"
        "                    if i > 100:\n"
        "                        pass\n"
    )
    issues = check_complexity(_parse(source), Path("f.py"), "pkg")
    assert len(issues) == 1
    assert issues[0].rule == "high-complexity"
    assert issues[0].severity == "warning"


def test_check_complexity_error_at_extreme():
    # Use separate if statements and bool operations to push complexity > 15
    lines = [
        "def extreme(x: int, a: bool, b: bool, c: bool) -> int:",
    ]
    for i in range(12):
        lines.append(f"    if x == {i}:")
        lines.append("        pass")
    lines.append("    if a and b and c:")
    lines.append("        pass")
    lines.append("    try:")
    lines.append("        pass")
    lines.append("    except ValueError:")
    lines.append("        pass")
    source = "\n".join(lines) + "\n"
    issues = check_complexity(_parse(source), Path("f.py"), "pkg")
    assert len(issues) == 1
    assert issues[0].severity == "error"


def test_check_complexity_ignores_simple_function():
    source = "def simple(x: int) -> int:\n    return x + 1\n"
    issues = check_complexity(_parse(source), Path("f.py"), "pkg")
    assert issues == []


# ── Missing Type Hints ───────────────────────────────────────────────────────


def test_check_missing_types_flags_return_type():
    source = "def no_return_type(x: int):\n    return x\n"
    issues = check_missing_types(_parse(source), Path("f.py"), "pkg")
    return_issues = [i for i in issues if i.rule == "missing-return-type"]
    assert len(return_issues) == 1


def test_check_missing_types_flags_param_type():
    source = "def no_param_type(x) -> int:\n    return x\n"
    issues = check_missing_types(_parse(source), Path("f.py"), "pkg")
    param_issues = [i for i in issues if i.rule == "missing-param-type"]
    assert len(param_issues) == 1


def test_check_missing_types_skips_private_and_self():
    source = "def _private(self, x):\n    pass\n"
    issues = check_missing_types(_parse(source), Path("f.py"), "pkg")
    assert issues == []


def test_check_missing_types_skips_init():
    source = "class C:\n    def __init__(self, x):\n        self.x = x\n"
    issues = check_missing_types(_parse(source), Path("f.py"), "pkg")
    assert issues == []


def test_check_missing_types_clean_when_typed():
    source = "def typed(x: int, y: str) -> bool:\n    return True\n"
    issues = check_missing_types(_parse(source), Path("f.py"), "pkg")
    assert issues == []


# ── Too Many Params ──────────────────────────────────────────────────────────


def test_check_excessive_params_flags_many_params():
    params = ", ".join(f"p{i}: int" for i in range(MAX_FUNC_PARAMS + 2))
    source = f"def func({params}) -> None:\n    pass\n"
    issues = check_excessive_params(_parse(source), Path("f.py"), "pkg")
    assert len(issues) == 1
    assert issues[0].rule == "too-many-params"


def test_check_excessive_params_includes_kwargs():
    source = (
        "def func(a: int, b: int, c: int, d: int, e: int, f: int, "
        "*args: int, **kwargs: int) -> None:\n"
        "    pass\n"
    )
    issues = check_excessive_params(_parse(source), Path("f.py"), "pkg")
    assert len(issues) == 1


def test_check_excessive_params_clean():
    source = "def func(a: int, b: str, c: float) -> None:\n    pass\n"
    issues = check_excessive_params(_parse(source), Path("f.py"), "pkg")
    assert issues == []


# ── Excessive Returns ────────────────────────────────────────────────────────


def test_check_excessive_returns_flags_many():
    source = (
        "def func(x: int) -> int:\n"
        "    if x == 1:\n"
        "        return 1\n"
        "    if x == 2:\n"
        "        return 2\n"
        "    if x == 3:\n"
        "        return 3\n"
        "    return 0\n"
    )
    issues = check_excessive_returns(_parse(source), Path("f.py"), "pkg")
    assert len(issues) == 1
    assert issues[0].rule == "excessive-returns"


def test_check_excessive_returns_clean():
    source = "def func(x: int) -> int:\n    if x > 0:\n        return 1\n    return 0\n"
    issues = check_excessive_returns(_parse(source), Path("f.py"), "pkg")
    assert issues == []


# ── Boolean Flag Params ──────────────────────────────────────────────────────


def test_check_boolean_flags_flags_many():
    source = (
        "def func(a: int = False, b: str = '', c: bool = True, "
        "d: bool = False, e: bool = True) -> None:\n"
        "    pass\n"
    )
    issues = check_boolean_flag_params(_parse(source), Path("f.py"), "pkg")
    assert len(issues) == 1
    assert issues[0].rule == "boolean-flag-params"


def test_check_boolean_flags_clean():
    source = "def func(a: int = 0, b: str = '', c: float = 1.0) -> None:\n    pass\n"
    issues = check_boolean_flag_params(_parse(source), Path("f.py"), "pkg")
    assert issues == []


# ── Untyped Dict ─────────────────────────────────────────────────────────────


def test_check_untyped_dicts_flags_bare_dict():
    source = "def func(x: dict) -> dict:\n    return x\n"
    issues = check_untyped_dicts(_parse(source), Path("f.py"), "pkg")
    # Both `dict` references are on line 1 — deduplicated to a single issue
    untyped = [i for i in issues if i.rule == "untyped-dict"]
    assert len(untyped) == 1


def test_check_untyped_dicts_allows_parameterized():
    source = "def func(x: dict[str, int]) -> dict[str, Any]:\n    return x\n"
    issues = check_untyped_dicts(_parse(source), Path("f.py"), "pkg")
    assert issues == []


def test_check_untyped_dicts_flags_annotated_variable():
    source = "x: dict = {}\n"
    issues = check_untyped_dicts(_parse(source), Path("f.py"), "pkg")
    assert len(issues) == 1


# ── Unused Imports ───────────────────────────────────────────────────────────


def test_check_unused_imports_flags_unused():
    source = "import os\nimport sys\nprint(sys.argv)\n"
    issues = check_unused_imports(_parse(source), Path("f.py"), "pkg")
    assert len(issues) == 1
    assert "os" in issues[0].message


def test_check_unused_imports_skips_init():
    source = "import os\n"
    issues = check_unused_imports(_parse(source), Path("__init__.py"), "pkg")
    assert issues == []


def test_check_unused_imports_clean():
    source = "import os\nprint(os.sep)\n"
    issues = check_unused_imports(_parse(source), Path("f.py"), "pkg")
    assert issues == []


def test_check_unused_imports_skips_future():
    source = "from __future__ import annotations\nx: int = 1\n"
    issues = check_unused_imports(_parse(source), Path("f.py"), "pkg")
    assert issues == []


# ── Swallowed Exceptions ────────────────────────────────────────────────────


def test_check_swallowed_flags_pass():
    source = "def f():\n    try:\n        pass\n    except Exception:\n        pass\n"
    issues = check_swallowed_exceptions(_parse(source), Path("f.py"), "pkg")
    assert len(issues) == 1
    assert issues[0].rule == "swallowed-exception"


def test_check_swallowed_flags_ellipsis():
    source = "def f():\n    try:\n        pass\n    except Exception:\n        ...\n"
    issues = check_swallowed_exceptions(_parse(source), Path("f.py"), "pkg")
    assert len(issues) == 1


def test_check_swallowed_allows_log():
    source = "def f():\n    try:\n        pass\n    except Exception as e:\n        print(e)\n"
    issues = check_swallowed_exceptions(_parse(source), Path("f.py"), "pkg")
    assert issues == []


# ── Duplicate Branches ──────────────────────────────────────────────────────


def test_check_duplicate_branches_flags_identical():
    source = "def f():\n    if True:\n        x = 1\n    else:\n        x = 1\n"
    issues = check_duplicate_branches(_parse(source), Path("f.py"), "pkg")
    assert len(issues) == 1
    assert issues[0].rule == "duplicate-branch"


def test_check_duplicate_branches_skips_elif():
    source = "def f():\n    if True:\n        x = 1\n    elif False:\n        x = 1\n"
    issues = check_duplicate_branches(_parse(source), Path("f.py"), "pkg")
    assert issues == []


def test_check_duplicate_branches_clean():
    source = "def f():\n    if True:\n        x = 1\n    else:\n        x = 2\n"
    issues = check_duplicate_branches(_parse(source), Path("f.py"), "pkg")
    assert issues == []


# ── Encapsulation Violations ─────────────────────────────────────────────────


def test_check_encapsulation_flags_private_access():
    source = (
        "class Foo:\n"
        "    def __init__(self):\n"
        "        self._x = 1\n"
        "\n"
        "class Bar:\n"
        "    def method(self, foo: Foo) -> int:\n"
        "        return foo._x\n"
    )
    issues = check_encapsulation_violations(_parse(source), Path("f.py"), "pkg")
    assert len(issues) == 1
    assert issues[0].rule == "encapsulation-violation"


def test_check_encapsulation_flags_getattr_private():
    # foo is the *second* parameter here (not the enclosing function's own
    # first), so the first-parameter exemption below doesn't apply — this
    # is still a genuine reflective violation into an unrelated object.
    source = (
        "class Foo:\n"
        "    def __init__(self):\n"
        "        self._x = 1\n"
        "\n"
        "def bar(label: str, foo: Foo) -> int:\n"
        "    return getattr(foo, '_x')\n"
    )
    issues = check_encapsulation_violations(_parse(source), Path("f.py"), "pkg")
    assert len(issues) == 1


def test_check_encapsulation_allows_first_param_direct_access():
    # A free function taking the "owning" instance explicitly as its first
    # parameter (the extracted-__init__/method pattern, e.g.
    # boti_data.gateway._gateway_init) is the free-function equivalent of
    # self access, not a violation.
    source = "class Gateway:\n    pass\n\ndef _init_resource(gateway: Gateway, config) -> None:\n    gateway._strategy.build(config)\n"
    issues = check_encapsulation_violations(_parse(source), Path("f.py"), "pkg")
    assert issues == []


def test_check_encapsulation_allows_first_param_reflective_access():
    source = "class Gateway:\n    pass\n\ndef _init_resource(gateway: Gateway) -> None:\n    setattr(gateway, '_strategy', None)\n"
    issues = check_encapsulation_violations(_parse(source), Path("f.py"), "pkg")
    assert issues == []


def test_check_encapsulation_still_flags_non_first_param_access():
    # Sanity check: the exemption is positional, not "any parameter" — a
    # second-positional parameter's private state is still a violation.
    source = "class Gateway:\n    pass\n\ndef combine(label: str, gateway: Gateway) -> None:\n    gateway._strategy.build()\n"
    issues = check_encapsulation_violations(_parse(source), Path("f.py"), "pkg")
    assert len(issues) == 1


def test_check_encapsulation_allows_self():
    source = "class Foo:\n    def method(self) -> int:\n        return self._x\n"
    issues = check_encapsulation_violations(_parse(source), Path("f.py"), "pkg")
    assert issues == []


def test_check_encapsulation_allows_dunder():
    source = "class Foo:\n    def method(self) -> int:\n        return self.__dict__\n"
    issues = check_encapsulation_violations(_parse(source), Path("f.py"), "pkg")
    assert issues == []


# ── God Class ────────────────────────────────────────────────────────────────


def test_check_god_class_flags_many_methods():
    methods = "\n".join(f"    def m{i}(self) -> None: pass" for i in range(MAX_CLASS_METHODS + 3))
    source = f"class GodClass:\n{methods}\n"
    issues = check_god_class(_parse(source), Path("f.py"), "pkg")
    assert len(issues) == 1
    assert issues[0].rule == "god-class"


def test_check_god_class_error_at_extreme():
    methods = "\n".join(
        f"    def m{i}(self) -> None: pass" for i in range(int(MAX_CLASS_METHODS * 1.5) + 1)
    )
    source = f"class GodClass:\n{methods}\n"
    issues = check_god_class(_parse(source), Path("f.py"), "pkg")
    assert len(issues) == 1
    assert issues[0].severity == "error"


def test_check_god_class_clean():
    source = "class SmallClass:\n    def m1(self) -> None: pass\n    def m2(self) -> None: pass\n"
    issues = check_god_class(_parse(source), Path("f.py"), "pkg")
    assert issues == []


def test_check_god_class_flags_high_wmc_with_few_methods():
    # A class can stay well under MAX_CLASS_METHODS/MAX_CLASS_ATTRS yet still
    # be a god class if its handful of methods are each individually complex.
    lines = ["class ComplexClass:", "    def m1(self, x) -> None:"]
    for i in range(MAX_CLASS_WMC + 2):
        lines.append(f"        if x == {i}:")
        lines.append("            pass")
    source = "\n".join(lines) + "\n"
    issues = check_god_class(_parse(source), Path("f.py"), "pkg")
    assert len(issues) == 1
    assert "WMC" in issues[0].message


# ── Low Cohesion ─────────────────────────────────────────────────────────────


def test_check_low_cohesion_flags_disjoint_clusters():
    source = (
        "class Foo:\n"
        "    def __init__(self):\n"
        "        self.a = 1\n"
        "        self.b = 2\n"
        "    def use_a(self):\n"
        "        return self.a\n"
        "    def use_b(self):\n"
        "        return self.b\n"
        "    def unrelated(self):\n"
        "        return 42\n"
    )
    issues = check_low_cohesion(_parse(source), Path("f.py"), "pkg")
    assert len(issues) == 1
    assert issues[0].rule == "low-cohesion"
    assert "LCOM4=2" in issues[0].message


def test_check_low_cohesion_allows_fully_connected_class():
    source = (
        "class Foo:\n"
        "    def __init__(self):\n"
        "        self.a = 1\n"
        "    def use_a(self):\n"
        "        return self.a\n"
        "    def also_use_a(self):\n"
        "        return self.a + 1\n"
    )
    issues = check_low_cohesion(_parse(source), Path("f.py"), "pkg")
    assert issues == []


def test_check_low_cohesion_skips_small_classes():
    # Below MIN_METHODS_FOR_COHESION — too little surface to meaningfully split.
    source = (
        "class Foo:\n"
        "    def a(self):\n"
        "        return self.x\n"
        "    def b(self):\n"
        "        return self.y\n"
    )
    assert MIN_METHODS_FOR_COHESION > 2
    issues = check_low_cohesion(_parse(source), Path("f.py"), "pkg")
    assert issues == []


def test_check_low_cohesion_allows_non_class():
    source = "def f():\n    pass\n"
    issues = check_low_cohesion(_parse(source), Path("f.py"), "pkg")
    assert issues == []


def test_check_low_cohesion_ignores_classmethods_and_staticmethods():
    # A Pydantic-style config class with a couple of @classmethod factories
    # and a @staticmethod helper must not be flagged just because those
    # never touch self — they're excluded from the cohesion graph entirely,
    # not counted as disconnected clusters.
    source = (
        "class Config:\n"
        "    def __init__(self):\n"
        "        self.a = 1\n"
        "    def use_a(self):\n"
        "        return self.a\n"
        "    @classmethod\n"
        "    def from_env(cls):\n"
        "        return cls()\n"
        "    @classmethod\n"
        "    def from_settings(cls, settings):\n"
        "        return cls()\n"
        "    @staticmethod\n"
        "    def validate(value):\n"
        "        return value\n"
    )
    issues = check_low_cohesion(_parse(source), Path("f.py"), "pkg")
    assert issues == []


def test_check_low_cohesion_allows_dataclass_value_object():
    # A small @dataclass hierarchy where each method touches only the
    # field(s) relevant to its own behavior is idiomatic, not a real
    # "doing too many unrelated things" smell — dataclasses have no
    # explicit __init__ body assigning fields together to anchor cohesion,
    # so this shape would otherwise look artificially disjoint.
    source = (
        "@dataclass(frozen=True)\n"
        "class TrueExpr(Expr):\n"
        "    def is_trivial(self) -> bool:\n"
        "        return True\n"
        "    def mask(self, df):\n"
        "        return df.map_partitions(lambda p: p)\n"
        "    def to_sqlalchemy_condition(self, model):\n"
        "        return true()\n"
    )
    issues = check_low_cohesion(_parse(source), Path("f.py"), "pkg")
    assert issues == []


def test_check_low_cohesion_ignores_pure_pass_through_methods():
    # A Facade class whose methods delegate to a free function taking the
    # instance explicitly (e.g. DataGateway.load() -> core_load.load_sync(
    # self, options)) never touches self.<attr> in those methods — that's
    # routing, not a real cohesion signal, so it shouldn't count them as
    # disconnected clusters.
    source = (
        "class Gateway:\n"
        "    def __init__(self):\n"
        "        self.a = 1\n"
        "    def use_a(self):\n"
        "        return self.a\n"
        "    def load(self, **options):\n"
        "        return core_load.load_sync(self, options)\n"
        "    def aload(self, **options):\n"
        "        return core_load.load_async(self, options)\n"
        "    def semi_join(self, other):\n"
        "        return core_load.semi_join_sync(self, other)\n"
    )
    issues = check_low_cohesion(_parse(source), Path("f.py"), "pkg")
    assert issues == []


def test_check_low_cohesion_ignores_abc_interface():
    # A deliberately stateless interface declaration (ABC/Protocol) has
    # nothing for LCOM4 to meaningfully measure — each @abstractmethod
    # necessarily touches no shared state since there's no state at all.
    source = (
        "class BackendStrategy(ABC):\n"
        "    @abstractmethod\n"
        "    def build_config(self, **kwargs): ...\n"
        "    @abstractmethod\n"
        "    def build_resource(self, config): ...\n"
        "    @abstractmethod\n"
        "    def load_structured_sync(self, ctx): ...\n"
        "    @abstractmethod\n"
        "    def load_configured_sync(self, ctx): ...\n"
    )
    issues = check_low_cohesion(_parse(source), Path("f.py"), "pkg")
    assert issues == []


def test_check_low_cohesion_ignores_protocol_interface():
    source = (
        "class Sink(Protocol):\n"
        "    def write(self, frame): ...\n"
        "    def awrite(self, frame): ...\n"
        "    def close(self): ...\n"
        "    def aclose(self): ...\n"
    )
    issues = check_low_cohesion(_parse(source), Path("f.py"), "pkg")
    assert issues == []


def test_check_low_cohesion_ignores_stateless_strategy_class():
    # A concrete Strategy-pattern implementation with zero instance state
    # (no self.<attr> anywhere) has nothing for LCOM4 to measure — every
    # method would otherwise become its own cluster purely because there's
    # no state to share, which isn't a real cohesion problem.
    source = (
        "class SqlAlchemyStrategy(BackendStrategy):\n"
        "    def build_config(self, **kwargs):\n"
        "        return SqlDatabaseConfig(**kwargs)\n"
        "    def build_resource(self, config):\n"
        "        return build_sql_resource(config)\n"
        "    def load_structured_sync(self, ctx):\n"
        "        return self._aload_sql(ctx)\n"
        "    def supports_in_chunk_hinting(self):\n"
        "        return True\n"
    )
    issues = check_low_cohesion(_parse(source), Path("f.py"), "pkg")
    assert issues == []


def test_check_low_cohesion_ignores_mostly_stateless_class():
    # A near-hard-zero case: almost every method is genuinely stateless,
    # but one references a sibling method as a bare callable (the
    # asyncio.to_thread(self.other_method, ...) idiom) rather than field
    # access — a hard "zero attrs" check would miss this; the fractional
    # threshold still exempts it since 1-in-6 is well under
    # MIN_STATEFUL_METHOD_FRACTION.
    # Two-statement bodies here deliberately, so none of these accidentally
    # qualify as pure-pass-through (and get excluded from the method count
    # entirely) the way a single `return f(x)` statement would.
    source = (
        "class SqlAlchemyStrategy(BackendStrategy):\n"
        "    def build_config(self, **kwargs):\n"
        "        cfg = SqlDatabaseConfig(**kwargs)\n"
        "        return cfg\n"
        "    def build_resource(self, config):\n"
        "        resource = build_sql_resource(config)\n"
        "        return resource\n"
        "    def load_structured_sync(self, ctx):\n"
        "        result = execute(ctx)\n"
        "        return result\n"
        "    async def load_structured_async(self, ctx):\n"
        "        return await asyncio.to_thread(self.load_structured_sync, ctx)\n"
        "    def supports_in_chunk_hinting(self):\n"
        "        return True\n"
        "    def chunk_hint(self, ctx):\n"
        "        return None\n"
    )
    issues = check_low_cohesion(_parse(source), Path("f.py"), "pkg")
    assert issues == []


def test_check_low_cohesion_ignores_method_calls_as_shared_state():
    # self.<name>(...) is a method *call* (an invocation relationship), not
    # a shared data field — two methods that each call a different private
    # helper aren't cohesive just because both reference some self.<attr>.
    # Without this distinction, LoadPlanner-shaped classes (many small
    # methods, each calling a different differently-named private helper,
    # no shared data fields at all) would look artificially disjoint.
    source = (
        "class LoadPlanner:\n"
        "    def plan_a(self, ctx):\n"
        "        return self._resolve_a(ctx)\n"
        "    def plan_b(self, ctx):\n"
        "        return self._resolve_b(ctx)\n"
        "    def plan_c(self, ctx):\n"
        "        return self._resolve_c(ctx)\n"
        "    def _resolve_a(self, ctx):\n"
        "        return ctx\n"
        "    def _resolve_b(self, ctx):\n"
        "        return ctx\n"
        "    def _resolve_c(self, ctx):\n"
        "        return ctx\n"
    )
    issues = check_low_cohesion(_parse(source), Path("f.py"), "pkg")
    assert issues == []


def test_check_low_cohesion_still_flags_real_split_with_method_calls_present():
    # Sanity check: excluding method-call targets from the attr graph must
    # not swallow genuine hits — a class with two real disjoint data-field
    # clusters is still flagged even when some methods also call helpers.
    source = (
        "class Foo:\n"
        "    def __init__(self):\n"
        "        self.a = 1\n"
        "        self.b = 2\n"
        "    def use_a(self):\n"
        "        self._log(self.a)\n"
        "    def use_b(self):\n"
        "        self._log(self.b)\n"
        "    def _log(self, value):\n"
        "        return value\n"
    )
    issues = check_low_cohesion(_parse(source), Path("f.py"), "pkg")
    assert len(issues) == 1
    assert "LCOM4=2" in issues[0].message


# ── Layer Violations ─────────────────────────────────────────────────────────


def test_check_layer_violations_flags_forbidden_import(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(ds, "PACKAGES", {"etl-demo": Path("/fake/etl-demo/src/etl_demo")})
    pkg_dir = Path("/fake/etl-demo/src/etl_demo/routes")
    source = "from boti_data.db.sql_resource import AsyncSqlDatabaseResource\n"
    issues = check_layer_violations(_parse(source), pkg_dir / "handler.py", "etl-demo")
    assert len(issues) == 1
    assert issues[0].rule == "layer-violation"
    assert issues[0].severity == "error"


def test_check_layer_violations_clean(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(ds, "PACKAGES", {"etl-demo": Path("/fake/etl-demo/src/etl_demo")})
    pkg_dir = Path("/fake/etl-demo/src/etl_demo/routes")
    source = "from etl_demo.cubes.mobile import MOBILE_CUBE\n"
    issues = check_layer_violations(_parse(source), pkg_dir / "handler.py", "etl-demo")
    assert issues == []


def test_check_layer_violations_skips_unknown_package():
    source = "from something import x\n"
    issues = check_layer_violations(_parse(source), Path("f.py"), "unknown-pkg")
    assert issues == []


# ── Transport in Library ────────────────────────────────────────────────────


def test_check_transport_flags_fastapi():
    source = "from fastapi import APIRouter\n"
    issues = check_transport_in_library(_parse(source), Path("f.py"), "etl-core")
    assert len(issues) == 1
    assert issues[0].rule == "transport-in-library"
    assert issues[0].severity == "error"


def test_check_transport_allows_non_library():
    source = "from fastapi import APIRouter\n"
    issues = check_transport_in_library(_parse(source), Path("f.py"), "etl-demo")
    assert issues == []


def test_check_transport_clean():
    source = "import pandas as pd\n"
    issues = check_transport_in_library(_parse(source), Path("f.py"), "etl-core")
    assert issues == []


# ── Potential Circular Import ────────────────────────────────────────────────


def test_check_circular_imports_flags_child_importing_parent(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(ds, "PACKAGES", {"mypkg": Path("/fake/mypkg/src/mypkg")})
    source = "from mypkg.sub import helper\n"
    filepath = Path("/fake/mypkg/src/mypkg/sub/module.py")
    issues = check_circular_imports(_parse(source), filepath, "mypkg")
    assert len(issues) == 1
    assert issues[0].rule == "potential-circular-import"


def test_check_circular_imports_clean(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(ds, "PACKAGES", {"mypkg": Path("/fake/mypkg/src/mypkg")})
    source = "from mypkg.sub.helper import something\n"
    filepath = Path("/fake/mypkg/src/mypkg/base.py")
    issues = check_circular_imports(_parse(source), filepath, "mypkg")
    assert issues == []


# ── God Module ──────────────────────────────────────────────────────────────


def test_check_god_module_flags_many_symbols():
    funcs = "\n".join(f"def func{i}() -> None: pass" for i in range(16))
    issues = check_god_module(_parse(funcs), Path("f.py"), "pkg")
    assert len(issues) == 1
    assert issues[0].rule == "god-module"


def test_check_god_module_clean():
    funcs = "\n".join(f"def func{i}() -> None: pass" for i in range(10))
    issues = check_god_module(_parse(funcs), Path("f.py"), "pkg")
    assert issues == []


# ── Mutable Default ─────────────────────────────────────────────────────────


def test_check_mutable_defaults_flags_list():
    source = "def func(x: list = []) -> None: pass\n"
    issues = check_mutable_defaults(_parse(source), Path("f.py"), "pkg")
    assert len(issues) == 1
    assert issues[0].rule == "mutable-default"


def test_check_mutable_defaults_flags_dict():
    source = "def func(x: dict = {}) -> None: pass\n"
    issues = check_mutable_defaults(_parse(source), Path("f.py"), "pkg")
    assert len(issues) == 1


def test_check_mutable_defaults_flags_set():
    source = "def func(x: set = set()) -> None: pass\n"
    issues = check_mutable_defaults(_parse(source), Path("f.py"), "pkg")
    assert issues == []  # set() is a call, not a literal


def test_check_mutable_defaults_clean():
    source = "def func(x: list | None = None) -> None: pass\n"
    issues = check_mutable_defaults(_parse(source), Path("f.py"), "pkg")
    assert issues == []


# ── Star Import ─────────────────────────────────────────────────────────────


def test_check_star_imports_flags():
    source = "from os import *\n"
    issues = check_star_imports(_parse(source), Path("f.py"), "pkg")
    assert len(issues) == 1
    assert issues[0].rule == "star-import"


def test_check_star_imports_clean():
    source = "from os import path, sep\n"
    issues = check_star_imports(_parse(source), Path("f.py"), "pkg")
    assert issues == []


# ── Global Mutations ─────────────────────────────────────────────────────────


def test_check_global_mutations_flags_list():
    source = "MY_LIST = [1, 2, 3]\n"
    issues = check_global_mutations(_parse(source), Path("f.py"), "pkg")
    assert len(issues) == 1
    assert issues[0].rule == "global-mutable"


def test_check_global_mutations_flags_dict():
    source = "MY_DICT = {'a': 1}\n"
    issues = check_global_mutations(_parse(source), Path("f.py"), "pkg")
    assert len(issues) == 1


def test_check_global_mutations_skips_private():
    source = "_MY_LIST = [1, 2, 3]\n"
    issues = check_global_mutations(_parse(source), Path("f.py"), "pkg")
    assert issues == []


def test_check_global_mutations_skips_non_mutable():
    source = "MY_INT = 42\nMY_STR = 'hello'\n"
    issues = check_global_mutations(_parse(source), Path("f.py"), "pkg")
    assert issues == []


# ── Scope Mutation (Shared Mutable State) ───────────────────────────────────


def test_check_scope_mutations_flags_global_augassign():
    source = "counter = 0\ndef increment():\n    global counter\n    counter += 1\n"
    issues = check_scope_mutations(_parse(source), Path("f.py"), "pkg")
    assert len(issues) == 1
    assert issues[0].rule == "scope-mutation"
    assert "counter" in issues[0].message
    assert "global" in issues[0].message


def test_check_scope_mutations_flags_global_assign():
    source = "config = {}\ndef reload() -> None:\n    global config\n    config = load_config()\n"
    issues = check_scope_mutations(_parse(source), Path("f.py"), "pkg")
    assert len(issues) == 1
    assert issues[0].rule == "scope-mutation"


def test_check_scope_mutations_flags_nonlocal():
    source = (
        "def outer() -> None:\n"
        "    state = 0\n"
        "    def bump() -> None:\n"
        "        nonlocal state\n"
        "        state += 1\n"
    )
    issues = check_scope_mutations(_parse(source), Path("f.py"), "pkg")
    assert len(issues) == 1
    assert issues[0].rule == "scope-mutation"
    assert "nonlocal" in issues[0].message


def test_check_scope_mutations_skips_read_only():
    source = "counter = 0\ndef read() -> int:\n    global counter\n    return counter\n"
    issues = check_scope_mutations(_parse(source), Path("f.py"), "pkg")
    assert issues == []


def test_check_scope_mutations_skips_local_variable():
    source = "def func() -> None:\n    x = 10\n    x = x + 1\n"
    issues = check_scope_mutations(_parse(source), Path("f.py"), "pkg")
    assert issues == []


def test_check_scope_mutations_clean_when_no_global():
    source = "def func(a: int) -> int:\n    result = a * 2\n    return result\n"
    issues = check_scope_mutations(_parse(source), Path("f.py"), "pkg")
    assert issues == []


def test_check_scope_mutations_one_finding_per_function():
    source = (
        "counter = 0\n"
        "total = 0\n"
        "def bump() -> None:\n"
        "    global counter, total\n"
        "    counter += 1\n"
        "    total += 1\n"
    )
    issues = check_scope_mutations(_parse(source), Path("f.py"), "pkg")
    assert len(issues) == 1


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
    original_prefixes = ds.ALLOWED_IMPORT_PREFIXES.copy()
    ds.PACKAGES = {pkg_name: pkg_dir}
    ds.ALLOWED_IMPORT_PREFIXES[pkg_name] = [f"{pkg_name}."]
    try:
        issues = check_import_cycles_pkg(pkg_name, files)
        assert len(issues) >= 1
        assert issues[0].rule == "import-cycle"
        assert issues[0].severity == "error"
    finally:
        ds.PACKAGES = original_packages
        ds.ALLOWED_IMPORT_PREFIXES.update(original_prefixes)


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
    original_prefixes = ds.ALLOWED_IMPORT_PREFIXES.copy()
    ds.PACKAGES = {pkg_name: pkg_dir}
    ds.ALLOWED_IMPORT_PREFIXES[pkg_name] = [f"{pkg_name}."]
    try:
        issues = check_import_cycles_pkg(pkg_name, files)
        assert issues == []
    finally:
        ds.PACKAGES = original_packages
        ds.ALLOWED_IMPORT_PREFIXES.update(original_prefixes)


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
    original_prefixes = ds.ALLOWED_IMPORT_PREFIXES.copy()
    ds.PACKAGES = {pkg_name: pkg_dir}
    ds.ALLOWED_IMPORT_PREFIXES[pkg_name] = [f"{pkg_name}."]
    try:
        issues = check_module_coupling_pkg(pkg_name, files)
        assert len(issues) == 1
        assert issues[0].rule == "high-coupling"
        assert f"{pkg_name}.hub" in issues[0].message
    finally:
        ds.PACKAGES = original_packages
        ds.ALLOWED_IMPORT_PREFIXES.update(original_prefixes)


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
    original_prefixes = ds.ALLOWED_IMPORT_PREFIXES.copy()
    ds.PACKAGES = {pkg_name: pkg_dir}
    ds.ALLOWED_IMPORT_PREFIXES[pkg_name] = [f"{pkg_name}."]
    try:
        issues = check_module_coupling_pkg(pkg_name, files)
        assert issues == []
    finally:
        ds.PACKAGES = original_packages
        ds.ALLOWED_IMPORT_PREFIXES.update(original_prefixes)


def test_check_module_coupling_pkg_clean(tmp_path: Path):
    pkg_dir = tmp_path / "clean_pkg"
    pkg_dir.mkdir()
    (pkg_dir / "__init__.py").write_text("")
    (pkg_dir / "a.py").write_text("x = 1\n")
    (pkg_dir / "b.py").write_text("from .a import x\n")

    pkg_name = "clean_pkg"
    files = _files_for_pkg(pkg_dir)

    original_packages = ds.PACKAGES
    original_prefixes = ds.ALLOWED_IMPORT_PREFIXES.copy()
    ds.PACKAGES = {pkg_name: pkg_dir}
    ds.ALLOWED_IMPORT_PREFIXES[pkg_name] = [f"{pkg_name}."]
    try:
        issues = check_module_coupling_pkg(pkg_name, files)
        assert issues == []
    finally:
        ds.PACKAGES = original_packages
        ds.ALLOWED_IMPORT_PREFIXES.update(original_prefixes)


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


# ── Gap-analysis rules ──────────────────────────────────────────────────────
# Tests for the 7 new rules added following the PyExamine / smellcheck /
# DPy / code-quality-analyzer gap analysis.


# ── Rule: dead-code ─────────────────────────────────────────────────────────


def test_check_dead_code_flags_after_return():
    source = "def f():\n    return 1\n    x = 2\n"
    issues = check_dead_code(_parse(source), Path("f.py"), "pkg")
    assert len(issues) == 1
    assert issues[0].rule == "dead-code"
    assert issues[0].severity == "warning"
    assert issues[0].line == 3


def test_check_dead_code_flags_after_raise():
    source = "def f():\n    raise ValueError('bad')\n    x = 2\n"
    issues = check_dead_code(_parse(source), Path("f.py"), "pkg")
    assert len(issues) == 1
    assert issues[0].rule == "dead-code"


def test_check_dead_code_flags_after_break():
    source = "def f():\n    for i in range(10):\n        break\n        x = i\n"
    issues = check_dead_code(_parse(source), Path("f.py"), "pkg")
    assert len(issues) == 1
    assert issues[0].rule == "dead-code"


def test_check_dead_code_flags_after_continue():
    source = "def f():\n    for i in range(10):\n        continue\n        x = i\n"
    issues = check_dead_code(_parse(source), Path("f.py"), "pkg")
    assert len(issues) == 1
    assert issues[0].rule == "dead-code"


def test_check_dead_code_flags_multiple_lines_after_return():
    source = "def f():\n    return\n    x = 2\n    y = 3\n"
    issues = check_dead_code(_parse(source), Path("f.py"), "pkg")
    assert len(issues) == 2
    assert all(i.rule == "dead-code" for i in issues)


def test_check_dead_code_no_flag_when_no_terminator():
    source = "def f():\n    x = 1\n    y = 2\n    return x + y\n"
    issues = check_dead_code(_parse(source), Path("f.py"), "pkg")
    assert issues == []


def test_check_dead_code_flags_in_if_body():
    source = "def f():\n    if True:\n        return\n        x = 1\n"
    issues = check_dead_code(_parse(source), Path("f.py"), "pkg")
    assert len(issues) == 1


def test_check_dead_code_no_flag_for_class_body():
    # Dead code detection only runs inside function bodies, not class bodies
    source = "class C:\n    return 1\n    x = 2\n"
    issues = check_dead_code(_parse(source), Path("f.py"), "pkg")
    assert issues == []


# ── Rule: message-chain ─────────────────────────────────────────────────────


def test_check_message_chains_flags_deep_chain():
    source = "def f():\n    a.b().c().d().e()\n"
    issues = check_message_chains(_parse(source), Path("f.py"), "pkg")
    assert len(issues) == 1
    assert issues[0].rule == "message-chain"
    assert issues[0].severity == "info"
    assert "depth 4" in issues[0].message


def test_check_message_chains_allows_short_chain():
    source = "def f():\n    a.b().c()\n"
    issues = check_message_chains(_parse(source), Path("f.py"), "pkg")
    assert issues == []


def test_check_message_chains_flags_deep_attr_chain():
    source = "def f():\n    a.b.c.d.e\n"
    issues = check_message_chains(_parse(source), Path("f.py"), "pkg")
    assert len(issues) == 1
    assert issues[0].rule == "message-chain"


def test_check_message_chains_allows_single_attr():
    source = "def f():\n    a.b\n"
    issues = check_message_chains(_parse(source), Path("f.py"), "pkg")
    assert issues == []


# ── Rule: excessive-decorators ──────────────────────────────────────────────


def test_check_excessive_decorators_flags_many_decorators():
    source = "@d1\n@d2\n@d3\n@d4\ndef f():\n    pass\n"
    issues = check_excessive_decorators(_parse(source), Path("f.py"), "pkg")
    assert len(issues) == 1
    assert issues[0].rule == "excessive-decorators"
    assert issues[0].severity == "info"
    assert "4 decorators" in issues[0].message


def test_check_excessive_decorators_allows_three_decorators():
    source = "@d1\n@d2\n@d3\ndef f():\n    pass\n"
    issues = check_excessive_decorators(_parse(source), Path("f.py"), "pkg")
    assert issues == []


def test_check_excessive_decorators_flags_class():
    source = "@d1\n@d2\n@d3\n@d4\nclass C:\n    pass\n"
    issues = check_excessive_decorators(_parse(source), Path("f.py"), "pkg")
    assert len(issues) == 1
    assert "class" in issues[0].message


def test_check_excessive_decorators_no_decorator():
    source = "def f():\n    pass\n"
    issues = check_excessive_decorators(_parse(source), Path("f.py"), "pkg")
    assert issues == []


# ── Rule: magic-number ──────────────────────────────────────────────────────


def test_check_magic_numbers_flags_literal():
    source = "def f():\n    x = 42\n"
    issues = check_magic_numbers(_parse(source), source, Path("f.py"), "pkg")
    assert len(issues) == 1
    assert issues[0].rule == "magic-number"
    assert issues[0].severity == "info"
    assert "42" in issues[0].message


def test_check_magic_numbers_allows_zero_one_minus_one():
    source = "def f():\n    a = 0\n    b = 1\n    c = -1\n"
    issues = check_magic_numbers(_parse(source), source, Path("f.py"), "pkg")
    assert issues == []


def test_check_magic_numbers_skips_init():
    source = "class C:\n    def __init__(self):\n        self.x = 42\n"
    issues = check_magic_numbers(_parse(source), source, Path("f.py"), "pkg")
    assert issues == []


def test_check_magic_numbers_flags_float():
    source = "def f():\n    ratio = 0.75\n"
    issues = check_magic_numbers(_parse(source), source, Path("f.py"), "pkg")
    assert len(issues) == 1
    assert issues[0].rule == "magic-number"


def test_check_magic_numbers_no_flag_in_string():
    source = "def f():\n    x = '42'\n"
    issues = check_magic_numbers(_parse(source), source, Path("f.py"), "pkg")
    assert issues == []


def test_check_magic_numbers_skips_keyword_argument():
    source = "def f():\n    warnings.warn('x', UserWarning, stacklevel=2)\n"
    issues = check_magic_numbers(_parse(source), source, Path("f.py"), "pkg")
    assert issues == []


def test_check_magic_numbers_skips_default_parameter_value():
    source = "def f(max_attempts: int = 3, base_delay: float = 0.5):\n    pass\n"
    issues = check_magic_numbers(_parse(source), source, Path("f.py"), "pkg")
    assert issues == []


def test_check_magic_numbers_still_flags_positional_argument():
    source = "def f():\n    do_thing(42)\n"
    issues = check_magic_numbers(_parse(source), source, Path("f.py"), "pkg")
    assert len(issues) == 1
    assert "42" in issues[0].message


def test_check_magic_numbers_displays_octal_notation():
    # 0o600 and 384 parse to the identical int — the message must show the
    # literal as written in source, not its decimal AST value, or "384"
    # reads as an arbitrary number when it isn't.
    source = "def f():\n    os.chmod(path, 0o600)\n"
    issues = check_magic_numbers(_parse(source), source, Path("f.py"), "pkg")
    assert len(issues) == 1
    assert "0o600" in issues[0].message
    assert "384" not in issues[0].message


def test_check_magic_numbers_displays_hex_notation():
    source = "def f():\n    mask = 0x1F\n"
    issues = check_magic_numbers(_parse(source), source, Path("f.py"), "pkg")
    assert len(issues) == 1
    assert "0x1F" in issues[0].message


def test_check_magic_numbers_displays_underscored_notation():
    source = "def f():\n    batch = 1_000\n"
    issues = check_magic_numbers(_parse(source), source, Path("f.py"), "pkg")
    assert len(issues) == 1
    assert "1_000" in issues[0].message


# ── Rule: magic-string ───────────────────────────────────────────────────────


def test_check_magic_strings_flags_repeated_comparison():
    source = (
        "def f(status):\n"
        "    if status == 'pending':\n"
        "        return 1\n"
        "def g(status):\n"
        "    if status == 'pending':\n"
        "        return 2\n"
    )
    issues = check_magic_strings(_parse(source), Path("f.py"), "pkg")
    assert len(issues) == 2
    assert all(i.rule == "magic-string" for i in issues)
    assert all(i.severity == "info" for i in issues)
    assert all("'pending'" in i.message for i in issues)
    assert all("2 times" in i.message for i in issues)


def test_check_magic_strings_allows_single_comparison():
    source = "def f(status):\n    if status == 'pending':\n        return 1\n"
    issues = check_magic_strings(_parse(source), Path("f.py"), "pkg")
    assert issues == []


def test_check_magic_strings_allows_empty_string():
    source = "def f(x):\n    if x == '':\n        return 1\n    if x == '':\n        return 2\n"
    issues = check_magic_strings(_parse(source), Path("f.py"), "pkg")
    assert issues == []


def test_check_magic_strings_ignores_literal_to_literal_comparison():
    source = "def f():\n    if 'a' == 'a':\n        pass\n    if 'a' == 'a':\n        pass\n"
    issues = check_magic_strings(_parse(source), Path("f.py"), "pkg")
    assert issues == []


def test_check_magic_strings_ignores_membership_checks():
    source = "def f(path):\n    if 'x' in path:\n        pass\n    if 'x' in path:\n        pass\n"
    issues = check_magic_strings(_parse(source), Path("f.py"), "pkg")
    assert issues == []


def test_check_magic_strings_flags_literal_on_left_side():
    source = (
        "def f(status):\n"
        "    if 'pending' == status:\n"
        "        pass\n"
        "    if status == 'pending':\n"
        "        pass\n"
    )
    issues = check_magic_strings(_parse(source), Path("f.py"), "pkg")
    assert len(issues) == 2


def test_check_magic_strings_ignores_ast_identifier_field_access():
    source = (
        "def f(kw, t, target):\n"
        "    if kw.arg == 'allow_pickle':\n"
        "        pass\n"
        "    if t.id == 'allow_pickle':\n"
        "        pass\n"
        "    if target.attr == 'allow_pickle':\n"
        "        pass\n"
    )
    issues = check_magic_strings(_parse(source), Path("f.py"), "pkg")
    assert issues == []


def test_check_magic_strings_ignores_dunder_name():
    source = (
        "def f(name):\n"
        "    if name == '__init__':\n"
        "        pass\n"
        "    if name != '__init__':\n"
        "        pass\n"
    )
    issues = check_magic_strings(_parse(source), Path("f.py"), "pkg")
    assert issues == []


def test_check_magic_strings_still_flags_short_dunder_looking_string():
    # '____' has no content between the underscores — not a real dunder
    # name, just four underscores — so it should still be flagged as an
    # ordinary repeated string comparison.
    source = "def f(x):\n    if x == '____':\n        pass\n    if x == '____':\n        pass\n"
    issues = check_magic_strings(_parse(source), Path("f.py"), "pkg")
    assert len(issues) == 2


# ── Rule: missing-else ──────────────────────────────────────────────────────


def test_check_missing_else_flags_nontrivial_if():
    source = "def f():\n    if x:\n        a = 1\n        b = 2\n"
    issues = check_missing_else(_parse(source), Path("f.py"), "pkg")
    assert len(issues) == 1
    assert issues[0].rule == "missing-else"
    assert issues[0].severity == "info"


def test_check_missing_else_allows_if_else():
    source = "def f():\n    if x:\n        a = 1\n        b = 2\n    else:\n        pass\n"
    issues = check_missing_else(_parse(source), Path("f.py"), "pkg")
    assert issues == []


def test_check_missing_else_allows_single_statement_if():
    source = "def f():\n    if x:\n        a = 1\n"
    issues = check_missing_else(_parse(source), Path("f.py"), "pkg")
    assert issues == []


def test_check_missing_else_allows_if_elif():
    source = "def f():\n    if x:\n        a = 1\n        b = 2\n    elif y:\n        pass\n"
    issues = check_missing_else(_parse(source), Path("f.py"), "pkg")
    assert issues == []


def test_check_missing_else_allows_guard_clause_return():
    source = "def f():\n    if x:\n        a = 1\n        return\n    return a\n"
    assert check_missing_else(_parse(source), Path("f.py"), "pkg") == []


def test_check_missing_else_allows_guard_clause_raise():
    source = "def f():\n    if x:\n        a = 1\n        raise ValueError(a)\n"
    assert check_missing_else(_parse(source), Path("f.py"), "pkg") == []


def test_check_missing_else_allows_loop_skip_continue():
    source = "def f():\n    for x in y:\n        if x in seen:\n            a = 1\n            continue\n"
    assert check_missing_else(_parse(source), Path("f.py"), "pkg") == []


def test_check_missing_else_allows_loop_skip_break():
    source = (
        "def f():\n    for x in y:\n        if x is done:\n            a = 1\n            break\n"
    )
    assert check_missing_else(_parse(source), Path("f.py"), "pkg") == []


def test_check_missing_else_still_flags_non_terminated_if():
    # Sanity check: the terminator narrowing must not swallow genuine hits —
    # a 2+ statement if with no else/elif/terminator is still flagged.
    source = "def f():\n    if x:\n        a = 1\n        b = 2\n    return a + b\n"
    issues = check_missing_else(_parse(source), Path("f.py"), "pkg")
    assert len(issues) == 1


def test_check_missing_else_allows_guard_then_nested_if():
    # "if right type: compute, then if bad: record" — the outer if only
    # guards entry into the inner check; no real second branch is missing.
    source = (
        "def f():\n"
        "    if isinstance(x, Y):\n"
        "        a = 1\n"
        "        if a > 0:\n"
        "            issues.append(a)\n"
    )
    assert check_missing_else(_parse(source), Path("f.py"), "pkg") == []


def test_check_missing_else_allows_guard_then_call():
    source = "def f():\n    if x:\n        a = 1\n        issues.append(a)\n"
    assert check_missing_else(_parse(source), Path("f.py"), "pkg") == []


def test_check_missing_else_allows_guard_then_loop():
    source = "def f():\n    if x:\n        a = get_items()\n        for i in a:\n            issues.append(i)\n"
    assert check_missing_else(_parse(source), Path("f.py"), "pkg") == []


def test_check_missing_else_still_flags_guard_then_plain_assignment():
    # The narrowing is syntactic, not dataflow-aware: a trailing plain
    # assignment (not a call/loop/nested-if) is still flagged even though
    # it may, in context, just be overriding an already-initialized default.
    source = "def f():\n    if x:\n        a = 1\n        b = 2\n"
    issues = check_missing_else(_parse(source), Path("f.py"), "pkg")
    assert len(issues) == 1


def test_check_missing_else_allows_guard_then_attribute_assignment():
    # Lazy-init-then-cache idiom: mutating self.x (already-existing state)
    # needs no negative path — "leave it as is" is already the default.
    source = "def f():\n    if x is None:\n        x = compute()\n        self.x = x\n    return self.x\n"
    assert check_missing_else(_parse(source), Path("f.py"), "pkg") == []


def test_check_missing_else_allows_guard_then_subscript_assignment():
    source = "def f():\n    if endpoint:\n        kwargs['a'] = 1\n        kwargs['b'] = 2\n    return kwargs\n"
    assert check_missing_else(_parse(source), Path("f.py"), "pkg") == []


def test_check_missing_else_allows_guard_then_augassign_attribute():
    source = "def f():\n    if x:\n        a = 1\n        self.count += a\n"
    assert check_missing_else(_parse(source), Path("f.py"), "pkg") == []


def test_check_missing_else_allows_guard_then_yield():
    source = (
        "def f():\n"
        "    for c in items:\n"
        "        if c not in seen:\n"
        "            seen.add(c)\n"
        "            yield c\n"
    )
    assert check_missing_else(_parse(source), Path("f.py"), "pkg") == []


def test_check_missing_else_allows_guard_then_yield_from():
    source = "def f():\n    if x:\n        a = 1\n        yield from a\n"
    assert check_missing_else(_parse(source), Path("f.py"), "pkg") == []


def test_check_missing_else_still_flags_mixed_target_assignment():
    # Sanity check: a multi-target assignment must be *all* attribute/
    # subscript targets to qualify — `a = self.x = 1` still introduces a
    # fresh local (`a`), so it stays flagged.
    source = "def f():\n    if x:\n        a = 1\n        a = self.x = 2\n"
    issues = check_missing_else(_parse(source), Path("f.py"), "pkg")
    assert len(issues) == 1


# ── Rule: lazy-class ────────────────────────────────────────────────────────


def test_check_lazy_class_flags_zero_methods():
    source = "class C:\n    x = 1\n"
    issues = check_lazy_class(_parse(source), Path("f.py"), "pkg")
    assert len(issues) == 1
    assert issues[0].rule == "lazy-class"
    assert issues[0].severity == "info"
    assert "0 method" in issues[0].message


def test_check_lazy_class_flags_one_method():
    source = "class C:\n    def f(self):\n        pass\n"
    issues = check_lazy_class(_parse(source), Path("f.py"), "pkg")
    assert len(issues) == 1
    assert issues[0].rule == "lazy-class"
    assert "1 method" in issues[0].message


def test_check_lazy_class_allows_two_methods():
    source = "class C:\n    def f(self):\n        pass\n    def g(self):\n        pass\n"
    issues = check_lazy_class(_parse(source), Path("f.py"), "pkg")
    assert issues == []


def test_check_lazy_class_allows_non_class():
    source = "def f():\n    pass\n"
    issues = check_lazy_class(_parse(source), Path("f.py"), "pkg")
    assert issues == []


def test_check_lazy_class_allows_pydantic_base_model():
    source = "class C(BaseModel):\n    x: int = 1\n"
    issues = check_lazy_class(_parse(source), Path("f.py"), "pkg")
    assert issues == []


def test_check_lazy_class_allows_pydantic_base_model_qualified():
    source = "class C(pydantic.BaseModel):\n    x: int = 1\n"
    issues = check_lazy_class(_parse(source), Path("f.py"), "pkg")
    assert issues == []


def test_check_lazy_class_allows_pydantic_base_settings():
    source = "class C(BaseSettings):\n    x: int = 1\n"
    issues = check_lazy_class(_parse(source), Path("f.py"), "pkg")
    assert issues == []


def test_check_lazy_class_allows_named_tuple():
    source = "class C(NamedTuple):\n    x: int\n"
    issues = check_lazy_class(_parse(source), Path("f.py"), "pkg")
    assert issues == []


def test_check_lazy_class_allows_named_tuple_qualified():
    source = "class C(typing.NamedTuple):\n    x: int\n"
    issues = check_lazy_class(_parse(source), Path("f.py"), "pkg")
    assert issues == []


def test_check_lazy_class_allows_dataclass_decorator():
    source = "@dataclass\nclass C:\n    x: int = 1\n"
    issues = check_lazy_class(_parse(source), Path("f.py"), "pkg")
    assert issues == []


def test_check_lazy_class_allows_dataclass_decorator_with_args():
    source = "@dataclass(frozen=True)\nclass C:\n    x: int = 1\n"
    issues = check_lazy_class(_parse(source), Path("f.py"), "pkg")
    assert issues == []


def test_check_lazy_class_still_flags_unrelated_base():
    # Sanity check: the pydantic/dataclass exemption must not swallow
    # genuine hits — an unrelated base class doesn't grant an exemption.
    source = "class C(SomeOtherBase):\n    x = 1\n"
    issues = check_lazy_class(_parse(source), Path("f.py"), "pkg")
    assert len(issues) == 1


def test_check_lazy_class_allows_builtin_exception_subclass():
    source = "class SchemaValidationError(TypeError):\n    '''Raised on bad schema.'''\n"
    issues = check_lazy_class(_parse(source), Path("f.py"), "pkg")
    assert issues == []


def test_check_lazy_class_allows_plain_exception_subclass():
    source = "class MyError(Exception):\n    pass\n"
    issues = check_lazy_class(_parse(source), Path("f.py"), "pkg")
    assert issues == []


def test_check_lazy_class_allows_custom_exception_hierarchy():
    # Not a builtin base, but named by the same Error/Exception/Warning
    # convention — covers subclassing a project's own exception base.
    source = "class NotFoundError(AppBaseError):\n    pass\n"
    issues = check_lazy_class(_parse(source), Path("f.py"), "pkg")
    assert issues == []


def test_check_lazy_class_allows_warning_subclass():
    source = "class DeprecatedFeatureWarning(UserWarning):\n    pass\n"
    issues = check_lazy_class(_parse(source), Path("f.py"), "pkg")
    assert issues == []


def test_check_lazy_class_allows_qualified_exception_subclass():
    source = "class Boom(builtins.RuntimeError):\n    pass\n"
    issues = check_lazy_class(_parse(source), Path("f.py"), "pkg")
    assert issues == []


# ── Rule: deep-inheritance ──────────────────────────────────────────────────


def test_check_deep_inheritance_flags_three_level_chain():
    source = (
        "class A:\n"
        "    pass\n"
        "class B(A):\n"
        "    pass\n"
        "class C(B):\n"
        "    pass\n"
        "class D(C):\n"
        "    pass\n"
        "class E(D):\n"
        "    pass\n"
    )
    issues = check_deep_inheritance(_parse(source), Path("f.py"), "pkg")
    assert len(issues) >= 1
    assert issues[0].rule == "deep-inheritance"
    assert issues[0].severity == "warning"


def test_check_deep_inheritance_allows_shallow():
    source = "class A:\n    pass\nclass B(A):\n    pass\n"
    issues = check_deep_inheritance(_parse(source), Path("f.py"), "pkg")
    assert issues == []


def test_check_deep_inheritance_no_bases():
    source = "class C:\n    pass\n"
    issues = check_deep_inheritance(_parse(source), Path("f.py"), "pkg")
    assert issues == []


def test_check_deep_inheritance_allows_single_level():
    source = "class Base:\n    pass\nclass Child(Base):\n    pass\n"
    issues = check_deep_inheritance(_parse(source), Path("f.py"), "pkg")
    assert issues == []


# ── Rule: pass-through-method ─────────────────────────────────────────────────


def test_check_pass_through_methods_flags_pure_delegation():
    source = "class Wrapper:\n    def get(self, key):\n        return self._inner.get(key)\n"
    issues = check_pass_through_methods(_parse(source), Path("f.py"), "pkg")
    assert len(issues) == 1
    assert issues[0].rule == "pass-through-method"
    assert issues[0].severity == "info"
    assert "_inner.get()" in issues[0].message


def test_check_pass_through_methods_flags_expression_statement_form():
    source = "class Wrapper:\n    def close(self):\n        self._inner.close()\n"
    issues = check_pass_through_methods(_parse(source), Path("f.py"), "pkg")
    assert len(issues) == 1


def test_check_pass_through_methods_flags_awaited_call():
    source = (
        "class Wrapper:\n    async def get(self, key):\n        return await self._inner.get(key)\n"
    )
    issues = check_pass_through_methods(_parse(source), Path("f.py"), "pkg")
    assert len(issues) == 1


def test_check_pass_through_methods_ignores_transformed_args():
    source = (
        "class Wrapper:\n    def get(self, key):\n        return self._inner.get(key.upper())\n"
    )
    issues = check_pass_through_methods(_parse(source), Path("f.py"), "pkg")
    assert issues == []


def test_check_pass_through_methods_ignores_multi_statement_body():
    source = (
        "class Wrapper:\n"
        "    def get(self, key):\n"
        "        log(key)\n"
        "        return self._inner.get(key)\n"
    )
    issues = check_pass_through_methods(_parse(source), Path("f.py"), "pkg")
    assert issues == []


def test_check_pass_through_methods_ignores_dunder_methods():
    source = "class Wrapper:\n    def __init__(self, inner):\n        self._inner = inner\n"
    issues = check_pass_through_methods(_parse(source), Path("f.py"), "pkg")
    assert issues == []


def test_check_pass_through_methods_ignores_super_call():
    source = "class Child(Base):\n    def get(self, key):\n        return super().get(key)\n"
    issues = check_pass_through_methods(_parse(source), Path("f.py"), "pkg")
    assert issues == []


def test_check_pass_through_methods_allows_docstring_only_body_to_still_flag():
    source = (
        "class Wrapper:\n"
        "    def get(self, key):\n"
        "        '''Docstring.'''\n"
        "        return self._inner.get(key)\n"
    )
    issues = check_pass_through_methods(_parse(source), Path("f.py"), "pkg")
    assert len(issues) == 1


# ── Remediation Priority ────────────────────────────────────────────────────


def test_compute_priority_score_error_high_effort():
    issue = Issue(
        file=Path("f.py"),
        line=1,
        severity="error",
        rule="import-cycle",
        message="circular import",
        package="pkg",
    )
    score = compute_priority_score(issue)
    # error=6.0 × effort=5.0 = 30.0
    assert score == 30.0


def test_compute_priority_score_warning_moderate_effort():
    issue = Issue(
        file=Path("f.py"),
        line=1,
        severity="warning",
        rule="long-function",
        message="too long",
        package="pkg",
    )
    score = compute_priority_score(issue)
    # warning=1.5 × effort=2.5 = 3.75
    assert score == 3.75


def test_compute_priority_score_info_trivial():
    issue = Issue(
        file=Path("f.py"),
        line=1,
        severity="info",
        rule="dead-code",
        message="unreachable",
        package="pkg",
    )
    score = compute_priority_score(issue)
    # info=0.3 × effort=0.5 = 0.15
    assert score == 0.15


def test_build_remediation_plan_groups_by_rule():
    issues = [
        Issue(Path("a.py"), 1, "error", "import-cycle", "cycle", "pkg"),
        Issue(Path("b.py"), 2, "error", "import-cycle", "cycle", "pkg"),
        Issue(Path("c.py"), 3, "warning", "long-function", "long", "pkg"),
    ]
    steps = build_remediation_plan(issues)
    assert len(steps) == 2
    # import-cycle has higher score (30.0) than long-function (3.75)
    assert steps[0].rule == "import-cycle"
    assert steps[0].count == 2
    assert steps[1].rule == "long-function"
    assert steps[1].count == 1


def test_build_remediation_plan_priority_labels():
    issues = [
        Issue(Path("a.py"), 1, "error", "god-class", "too big", "pkg"),
        Issue(Path("b.py"), 2, "info", "todo-marker", "todo", "pkg"),
    ]
    steps = build_remediation_plan(issues)
    assert steps[0].priority == "P0"  # error × effort=5.0 = 30.0
    assert steps[1].priority == "P3"  # info × effort=0.5 = 0.15


def test_plan_report_contains_all_sections():
    issues = [
        Issue(Path("a.py"), 1, "error", "import-cycle", "cycle", "pkg"),
        Issue(Path("b.py"), 2, "warning", "long-function", "long", "pkg"),
    ]
    report = plan_report(issues)
    assert "REMEDIATION PLAN" in report
    assert "RECOMMENDED FIX ORDER" in report
    assert "import-cycle" in report
    assert "long-function" in report
    assert "P0" in report or "P1" in report
    assert "P2" in report or "P3" in report


def test_plan_report_empty_issues():
    report = plan_report([])
    assert "All clean" in report


def test_plan_report_groups_files():
    issues = [
        Issue(Path("a.py"), 1, "warning", "long-function", "long", "pkg"),
        Issue(Path("a.py"), 2, "warning", "long-function", "long", "pkg"),
        Issue(Path("b.py"), 3, "warning", "long-function", "long", "pkg"),
    ]
    steps = build_remediation_plan(issues)
    assert len(steps) == 1
    assert steps[0].count == 3
    assert len(steps[0].files) == 2  # a.py and b.py


def test_plan_report_truncates_by_top():
    issues = [Issue(Path(f"file{i}.py"), 1, "info", f"rule-{i}", "msg", "pkg") for i in range(30)]
    report = plan_report(issues, top=5)
    assert "and 25 more rules" in report
