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
    body = "\n".join(f"    x{i} = {i}" for i in range(ds.MAX_FUNCTION_LINES + 5))
    source = f"def long_func():\n{body}\n    return x0\n"
    issues = ds.check_long_functions(_parse(source), Path("f.py"), "pkg")
    assert len(issues) == 1
    assert issues[0].rule == "long-function"
    assert issues[0].severity == "warning"
    assert "long_func" in issues[0].message


def test_check_long_functions_ignores_short_function():
    source = "def short_func():\n    return 1\n"
    issues = ds.check_long_functions(_parse(source), Path("f.py"), "pkg")
    assert issues == []


def test_check_deep_nesting_flags_excessive_nesting():
    nested = "if True:\n"
    indent = "    "
    for i in range(ds.MAX_NESTING_DEPTH + 2):
        nested += indent * (i + 1) + "if True:\n"
    nested += indent * (ds.MAX_NESTING_DEPTH + 3) + "pass\n"
    source = "def deeply_nested():\n" + "\n".join("    " + line for line in nested.splitlines())
    issues = ds.check_deep_nesting(_parse(source), Path("f.py"), "pkg")
    assert len(issues) == 1
    assert issues[0].rule == "deep-nesting"


def test_check_bare_except_flags_bare_except():
    source = "def f():\n    try:\n        pass\n    except:\n        pass\n"
    issues = ds.check_bare_except(_parse(source), Path("f.py"), "pkg")
    assert len(issues) == 1
    assert issues[0].rule == "bare-except"


def test_check_bare_except_allows_typed_except():
    source = "def f():\n    try:\n        pass\n    except ValueError:\n        pass\n"
    issues = ds.check_bare_except(_parse(source), Path("f.py"), "pkg")
    assert issues == []


def test_check_long_file_flags_over_threshold():
    source = "\n".join(f"x = {i}" for i in range(ds.MAX_FILE_LINES + 10))
    issues = ds.check_long_file(source, Path("f.py"), "pkg")
    assert len(issues) == 1
    assert issues[0].rule == "long-file"


def test_check_todo_markers_flags_todo_comment():
    source = "x = 1  # TODO: fix this later\n"
    issues = ds.check_todo_markers(source, Path("f.py"), "pkg")
    assert len(issues) == 1
    assert issues[0].rule == "todo-marker"


# ── compute_score() ──────────────────────────────────────────────────────────


def test_compute_score_perfect_for_empty_result():
    score, grade = ds.compute_score(ds.ScanResult())
    assert score == 100.0
    assert grade == "A"


def test_compute_score_degrades_with_errors():
    result = ds.ScanResult(
        issues=[
            ds.Issue(file=Path("f.py"), line=1, severity="error", rule="x", message="m")
            for _ in range(5)
        ],
        total_lines=1000,
    )
    score, grade = ds.compute_score(result)
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
        "fake_pkg", fake_package, exclude=[], min_duplicate_lines=5, twin_similarity=0.6
    )
    assert result.files_scanned == 2
    assert any(i.rule == "bare-except" for i in result.issues)
    assert all(i.package == "fake_pkg" for i in result.issues)


def test_scan_package_missing_path_returns_empty_result(tmp_path: Path):
    result = ds.scan_package(
        "missing",
        tmp_path / "does-not-exist",
        exclude=[],
        min_duplicate_lines=5,
        twin_similarity=0.6,
    )
    assert result.files_scanned == 0
    assert result.issues == []


def test_scan_package_respects_exclude(fake_package: Path):
    result = ds.scan_package(
        "fake_pkg",
        fake_package,
        exclude=["messy.py"],
        min_duplicate_lines=5,
        twin_similarity=0.6,
    )
    assert result.files_scanned == 1
    assert not any(i.rule == "bare-except" for i in result.issues)


# ── Inline suppression (# spaghetti-ignore) ──────────────────────────────────


def _scan_single_file(tmp_path: Path, source: str) -> ds.ScanResult:
    pkg_dir = tmp_path / "suppress_pkg"
    pkg_dir.mkdir()
    (pkg_dir / "mod.py").write_text(source)
    return ds.scan_package(
        "suppress_pkg", pkg_dir, exclude=[], min_duplicate_lines=5, twin_similarity=0.6
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

    def fake_scan_package(pkg_name, pkg_path, *, exclude, min_duplicate_lines, twin_similarity):
        captured.update(
            pkg_name=pkg_name,
            pkg_path=pkg_path,
            exclude=exclude,
            min_duplicate_lines=min_duplicate_lines,
            twin_similarity=twin_similarity,
        )
        return ds.ScanResult(files_scanned=1)

    monkeypatch.setattr(ds, "scan_package", fake_scan_package)

    async def run() -> ds.ScanResult:
        # Use ThreadPoolExecutor: ProcessPoolExecutor would fail because
        # fake_scan_package is a local closure that cannot be pickled.
        executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
        try:
            agent = ds.SpaghettiReviewAgent(
                "fake_pkg",
                fake_package,
                exclude=["x"],
                min_duplicate_lines=7,
                twin_similarity=0.5,
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
        "exclude": ["x"],
        "min_duplicate_lines": 7,
        "twin_similarity": 0.5,
    }


def test_agent_review_after_close_raises():
    async def run() -> None:
        executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
        try:
            agent = ds.SpaghettiReviewAgent(
                "fake_pkg",
                Path("/nonexistent"),
                exclude=[],
                min_duplicate_lines=5,
                twin_similarity=0.6,
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
                exclude=[],
                min_duplicate_lines=5,
                twin_similarity=0.6,
                executor=executor,
            )
        )
    finally:
        executor.shutdown(wait=False)
    assert set(results) == {"a", "b"}
    assert all(isinstance(r, ds.ScanResult) for r in results.values())
    assert all(r.files_scanned == 2 for r in results.values())


def test_review_packages_concurrently_actually_overlaps(monkeypatch: pytest.MonkeyPatch):
    """Deterministic proof of concurrency: if the packages were reviewed one
    at a time, only one call would ever be inside fake_scan_package
    simultaneously and the barrier would never reach `n` parties, timing out.
    This fails loudly (BrokenBarrierError) if a future change accidentally
    serializes the reviews instead of running them concurrently."""
    n = 5
    all_arrived = threading.Barrier(n, timeout=5)

    def fake_scan_package(pkg_name, pkg_path, *, exclude, min_duplicate_lines, twin_similarity):
        all_arrived.wait()
        return ds.ScanResult(files_scanned=1)

    monkeypatch.setattr(ds, "scan_package", fake_scan_package)
    pkg_names = [f"pkg{i}" for i in range(n)]
    monkeypatch.setattr(ds, "PACKAGES", {name: Path(f"/fake/{name}") for name in pkg_names})

    executor = concurrent.futures.ThreadPoolExecutor(max_workers=n)
    try:
        results = asyncio.run(
            ds.review_packages_concurrently(
                pkg_names,
                exclude=[],
                min_duplicate_lines=5,
                twin_similarity=0.6,
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

    def flaky_scan_package(pkg_name, pkg_path, *, exclude, min_duplicate_lines, twin_similarity):
        if pkg_name == "broken":
            raise RuntimeError("scan exploded")
        return ds.ScanResult(files_scanned=1)

    monkeypatch.setattr(ds, "scan_package", flaky_scan_package)
    monkeypatch.setattr(ds, "PACKAGES", {"ok": Path("/fake/ok"), "broken": Path("/fake/broken")})

    executor = concurrent.futures.ThreadPoolExecutor(max_workers=2)
    try:
        with pytest.raises(RuntimeError, match="scan exploded"):
            asyncio.run(
                ds.review_packages_concurrently(
                    ["ok", "broken"],
                    exclude=[],
                    min_duplicate_lines=5,
                    twin_similarity=0.6,
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

    packages = ds._load_packages_from_config(config_dir / "spaghetti.yaml")

    assert packages == {
        "my-lib": (tmp_path / "my-lib" / "src" / "my_lib").resolve(),
        "other": Path("/abs/other"),
    }


def test_load_packages_from_config_missing_packages_key_errors(tmp_path: Path):
    config_path = tmp_path / "spaghetti.yaml"
    config_path.write_text("not_packages: {}\n")

    with pytest.raises(SystemExit, match="must define a top-level 'packages' mapping"):
        ds._load_packages_from_config(config_path)


def test_load_packages_from_config_bad_yaml_errors(tmp_path: Path):
    config_path = tmp_path / "spaghetti.yaml"
    config_path.write_text("packages: [this, is, a, list, not, a, mapping]\n")

    with pytest.raises(SystemExit, match="must define a top-level 'packages' mapping"):
        ds._load_packages_from_config(config_path)


def test_load_packages_from_config_missing_file_errors(tmp_path: Path):
    with pytest.raises(SystemExit, match="could not read --config"):
        ds._load_packages_from_config(tmp_path / "does-not-exist.yaml")


def test_parse_package_args_resolves_relative_to_cwd(tmp_path: Path):
    packages = ds._parse_package_args(["my-lib=src/my_lib"], cwd=tmp_path)
    assert packages == {"my-lib": (tmp_path / "src" / "my_lib").resolve()}


def test_parse_package_args_multiple_entries(tmp_path: Path):
    packages = ds._parse_package_args(["a=src/a", "b=src/b"], cwd=tmp_path)
    assert packages == {
        "a": (tmp_path / "src" / "a").resolve(),
        "b": (tmp_path / "src" / "b").resolve(),
    }


@pytest.mark.parametrize("bad_entry", ["no-equals-sign", "=missing-name", "name="])
def test_parse_package_args_rejects_malformed_entries(tmp_path: Path, bad_entry: str):
    with pytest.raises(SystemExit, match="expects NAME=PATH"):
        ds._parse_package_args([bad_entry], cwd=tmp_path)


def test_resolve_packages_defaults_when_no_config_or_package_args(tmp_path: Path):
    defaults = {"a": Path("/fake/a")}
    result = ds.resolve_packages(config_path=None, package_args=[], defaults=defaults, cwd=tmp_path)
    assert result == defaults
    # Must be a copy, not the same object, so callers can't mutate defaults.
    assert result is not defaults


def test_resolve_packages_config_replaces_defaults_entirely(tmp_path: Path):
    config_path = tmp_path / "spaghetti.yaml"
    config_path.write_text("packages:\n  configured: src/configured\n")
    defaults = {"a": Path("/fake/a")}

    result = ds.resolve_packages(
        config_path=config_path, package_args=[], defaults=defaults, cwd=tmp_path
    )

    assert "a" not in result
    assert result == {"configured": (tmp_path / "src" / "configured").resolve()}


def test_resolve_packages_package_args_overlay_config(tmp_path: Path):
    config_path = tmp_path / "spaghetti.yaml"
    config_path.write_text("packages:\n  configured: src/configured\n")

    result = ds.resolve_packages(
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
    result = ds.resolve_packages(
        config_path=None,
        package_args=["only=src/only"],
        defaults={"a": Path("/fake/a")},
        cwd=tmp_path,
    )
    assert result == {"only": (tmp_path / "src" / "only").resolve()}


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
    issues = ds.check_complexity(_parse(source), Path("f.py"), "pkg")
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
    issues = ds.check_complexity(_parse(source), Path("f.py"), "pkg")
    assert len(issues) == 1
    assert issues[0].severity == "error"


def test_check_complexity_ignores_simple_function():
    source = "def simple(x: int) -> int:\n    return x + 1\n"
    issues = ds.check_complexity(_parse(source), Path("f.py"), "pkg")
    assert issues == []


# ── Missing Type Hints ───────────────────────────────────────────────────────


def test_check_missing_types_flags_return_type():
    source = "def no_return_type(x: int):\n    return x\n"
    issues = ds.check_missing_types(_parse(source), Path("f.py"), "pkg")
    return_issues = [i for i in issues if i.rule == "missing-return-type"]
    assert len(return_issues) == 1


def test_check_missing_types_flags_param_type():
    source = "def no_param_type(x) -> int:\n    return x\n"
    issues = ds.check_missing_types(_parse(source), Path("f.py"), "pkg")
    param_issues = [i for i in issues if i.rule == "missing-param-type"]
    assert len(param_issues) == 1


def test_check_missing_types_skips_private_and_self():
    source = "def _private(self, x):\n    pass\n"
    issues = ds.check_missing_types(_parse(source), Path("f.py"), "pkg")
    assert issues == []


def test_check_missing_types_skips_init():
    source = "class C:\n    def __init__(self, x):\n        self.x = x\n"
    issues = ds.check_missing_types(_parse(source), Path("f.py"), "pkg")
    assert issues == []


def test_check_missing_types_clean_when_typed():
    source = "def typed(x: int, y: str) -> bool:\n    return True\n"
    issues = ds.check_missing_types(_parse(source), Path("f.py"), "pkg")
    assert issues == []


# ── Too Many Params ──────────────────────────────────────────────────────────


def test_check_excessive_params_flags_many_params():
    params = ", ".join(f"p{i}: int" for i in range(ds.MAX_FUNC_PARAMS + 2))
    source = f"def func({params}) -> None:\n    pass\n"
    issues = ds.check_excessive_params(_parse(source), Path("f.py"), "pkg")
    assert len(issues) == 1
    assert issues[0].rule == "too-many-params"


def test_check_excessive_params_includes_kwargs():
    source = (
        "def func(a: int, b: int, c: int, d: int, e: int, f: int, "
        "*args: int, **kwargs: int) -> None:\n"
        "    pass\n"
    )
    issues = ds.check_excessive_params(_parse(source), Path("f.py"), "pkg")
    assert len(issues) == 1


def test_check_excessive_params_clean():
    source = "def func(a: int, b: str, c: float) -> None:\n    pass\n"
    issues = ds.check_excessive_params(_parse(source), Path("f.py"), "pkg")
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
    issues = ds.check_excessive_returns(_parse(source), Path("f.py"), "pkg")
    assert len(issues) == 1
    assert issues[0].rule == "excessive-returns"


def test_check_excessive_returns_clean():
    source = "def func(x: int) -> int:\n    if x > 0:\n        return 1\n    return 0\n"
    issues = ds.check_excessive_returns(_parse(source), Path("f.py"), "pkg")
    assert issues == []


# ── Boolean Flag Params ──────────────────────────────────────────────────────


def test_check_boolean_flags_flags_many():
    source = (
        "def func(a: int = False, b: str = '', c: bool = True, "
        "d: bool = False, e: bool = True) -> None:\n"
        "    pass\n"
    )
    issues = ds.check_boolean_flag_params(_parse(source), Path("f.py"), "pkg")
    assert len(issues) == 1
    assert issues[0].rule == "boolean-flag-params"


def test_check_boolean_flags_clean():
    source = "def func(a: int = 0, b: str = '', c: float = 1.0) -> None:\n    pass\n"
    issues = ds.check_boolean_flag_params(_parse(source), Path("f.py"), "pkg")
    assert issues == []


# ── Untyped Dict ─────────────────────────────────────────────────────────────


def test_check_untyped_dicts_flags_bare_dict():
    source = "def func(x: dict) -> dict:\n    return x\n"
    issues = ds.check_untyped_dicts(_parse(source), Path("f.py"), "pkg")
    # Both `dict` references are on line 1 — deduplicated to a single issue
    untyped = [i for i in issues if i.rule == "untyped-dict"]
    assert len(untyped) == 1


def test_check_untyped_dicts_allows_parameterized():
    source = "def func(x: dict[str, int]) -> dict[str, Any]:\n    return x\n"
    issues = ds.check_untyped_dicts(_parse(source), Path("f.py"), "pkg")
    assert issues == []


def test_check_untyped_dicts_flags_annotated_variable():
    source = "x: dict = {}\n"
    issues = ds.check_untyped_dicts(_parse(source), Path("f.py"), "pkg")
    assert len(issues) == 1


# ── Unused Imports ───────────────────────────────────────────────────────────


def test_check_unused_imports_flags_unused():
    source = "import os\nimport sys\nprint(sys.argv)\n"
    issues = ds.check_unused_imports(_parse(source), Path("f.py"), "pkg")
    assert len(issues) == 1
    assert "os" in issues[0].message


def test_check_unused_imports_skips_init():
    source = "import os\n"
    issues = ds.check_unused_imports(_parse(source), Path("__init__.py"), "pkg")
    assert issues == []


def test_check_unused_imports_clean():
    source = "import os\nprint(os.sep)\n"
    issues = ds.check_unused_imports(_parse(source), Path("f.py"), "pkg")
    assert issues == []


def test_check_unused_imports_skips_future():
    source = "from __future__ import annotations\nx: int = 1\n"
    issues = ds.check_unused_imports(_parse(source), Path("f.py"), "pkg")
    assert issues == []


# ── Swallowed Exceptions ────────────────────────────────────────────────────


def test_check_swallowed_flags_pass():
    source = "def f():\n    try:\n        pass\n    except Exception:\n        pass\n"
    issues = ds.check_swallowed_exceptions(_parse(source), Path("f.py"), "pkg")
    assert len(issues) == 1
    assert issues[0].rule == "swallowed-exception"


def test_check_swallowed_flags_ellipsis():
    source = "def f():\n    try:\n        pass\n    except Exception:\n        ...\n"
    issues = ds.check_swallowed_exceptions(_parse(source), Path("f.py"), "pkg")
    assert len(issues) == 1


def test_check_swallowed_allows_log():
    source = "def f():\n    try:\n        pass\n    except Exception as e:\n        print(e)\n"
    issues = ds.check_swallowed_exceptions(_parse(source), Path("f.py"), "pkg")
    assert issues == []


# ── Duplicate Branches ──────────────────────────────────────────────────────


def test_check_duplicate_branches_flags_identical():
    source = "def f():\n    if True:\n        x = 1\n    else:\n        x = 1\n"
    issues = ds.check_duplicate_branches(_parse(source), Path("f.py"), "pkg")
    assert len(issues) == 1
    assert issues[0].rule == "duplicate-branch"


def test_check_duplicate_branches_skips_elif():
    source = "def f():\n    if True:\n        x = 1\n    elif False:\n        x = 1\n"
    issues = ds.check_duplicate_branches(_parse(source), Path("f.py"), "pkg")
    assert issues == []


def test_check_duplicate_branches_clean():
    source = "def f():\n    if True:\n        x = 1\n    else:\n        x = 2\n"
    issues = ds.check_duplicate_branches(_parse(source), Path("f.py"), "pkg")
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
    issues = ds.check_encapsulation_violations(_parse(source), Path("f.py"), "pkg")
    assert len(issues) == 1
    assert issues[0].rule == "encapsulation-violation"


def test_check_encapsulation_flags_getattr_private():
    source = (
        "class Foo:\n"
        "    def __init__(self):\n"
        "        self._x = 1\n"
        "\n"
        "def bar(foo: Foo) -> int:\n"
        "    return getattr(foo, '_x')\n"
    )
    issues = ds.check_encapsulation_violations(_parse(source), Path("f.py"), "pkg")
    assert len(issues) == 1


def test_check_encapsulation_allows_self():
    source = "class Foo:\n    def method(self) -> int:\n        return self._x\n"
    issues = ds.check_encapsulation_violations(_parse(source), Path("f.py"), "pkg")
    assert issues == []


def test_check_encapsulation_allows_dunder():
    source = "class Foo:\n    def method(self) -> int:\n        return self.__dict__\n"
    issues = ds.check_encapsulation_violations(_parse(source), Path("f.py"), "pkg")
    assert issues == []


# ── God Class ────────────────────────────────────────────────────────────────


def test_check_god_class_flags_many_methods():
    methods = "\n".join(
        f"    def m{i}(self) -> None: pass" for i in range(ds.MAX_CLASS_METHODS + 3)
    )
    source = f"class GodClass:\n{methods}\n"
    issues = ds.check_god_class(_parse(source), Path("f.py"), "pkg")
    assert len(issues) == 1
    assert issues[0].rule == "god-class"


def test_check_god_class_error_at_extreme():
    methods = "\n".join(
        f"    def m{i}(self) -> None: pass" for i in range(int(ds.MAX_CLASS_METHODS * 1.5) + 1)
    )
    source = f"class GodClass:\n{methods}\n"
    issues = ds.check_god_class(_parse(source), Path("f.py"), "pkg")
    assert len(issues) == 1
    assert issues[0].severity == "error"


def test_check_god_class_clean():
    source = "class SmallClass:\n    def m1(self) -> None: pass\n    def m2(self) -> None: pass\n"
    issues = ds.check_god_class(_parse(source), Path("f.py"), "pkg")
    assert issues == []


# ── Layer Violations ─────────────────────────────────────────────────────────


def test_check_layer_violations_flags_forbidden_import(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(ds, "PACKAGES", {"etl-demo": Path("/fake/etl-demo/src/etl_demo")})
    pkg_dir = Path("/fake/etl-demo/src/etl_demo/routes")
    source = "from boti_data.db.sql_resource import AsyncSqlDatabaseResource\n"
    issues = ds.check_layer_violations(_parse(source), pkg_dir / "handler.py", "etl-demo")
    assert len(issues) == 1
    assert issues[0].rule == "layer-violation"
    assert issues[0].severity == "error"


def test_check_layer_violations_clean(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(ds, "PACKAGES", {"etl-demo": Path("/fake/etl-demo/src/etl_demo")})
    pkg_dir = Path("/fake/etl-demo/src/etl_demo/routes")
    source = "from etl_demo.cubes.mobile import MOBILE_CUBE\n"
    issues = ds.check_layer_violations(_parse(source), pkg_dir / "handler.py", "etl-demo")
    assert issues == []


def test_check_layer_violations_skips_unknown_package():
    source = "from something import x\n"
    issues = ds.check_layer_violations(_parse(source), Path("f.py"), "unknown-pkg")
    assert issues == []


# ── Transport in Library ────────────────────────────────────────────────────


def test_check_transport_flags_fastapi():
    source = "from fastapi import APIRouter\n"
    issues = ds.check_transport_in_library(_parse(source), Path("f.py"), "etl-core")
    assert len(issues) == 1
    assert issues[0].rule == "transport-in-library"
    assert issues[0].severity == "error"


def test_check_transport_allows_non_library():
    source = "from fastapi import APIRouter\n"
    issues = ds.check_transport_in_library(_parse(source), Path("f.py"), "etl-demo")
    assert issues == []


def test_check_transport_clean():
    source = "import pandas as pd\n"
    issues = ds.check_transport_in_library(_parse(source), Path("f.py"), "etl-core")
    assert issues == []


# ── Potential Circular Import ────────────────────────────────────────────────


def test_check_circular_imports_flags_child_importing_parent(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(ds, "PACKAGES", {"mypkg": Path("/fake/mypkg/src/mypkg")})
    source = "from mypkg.sub import helper\n"
    filepath = Path("/fake/mypkg/src/mypkg/sub/module.py")
    issues = ds.check_circular_imports(_parse(source), filepath, "mypkg")
    assert len(issues) == 1
    assert issues[0].rule == "potential-circular-import"


def test_check_circular_imports_clean(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(ds, "PACKAGES", {"mypkg": Path("/fake/mypkg/src/mypkg")})
    source = "from mypkg.sub.helper import something\n"
    filepath = Path("/fake/mypkg/src/mypkg/base.py")
    issues = ds.check_circular_imports(_parse(source), filepath, "mypkg")
    assert issues == []


# ── God Module ──────────────────────────────────────────────────────────────


def test_check_god_module_flags_many_symbols():
    funcs = "\n".join(f"def func{i}() -> None: pass" for i in range(16))
    issues = ds.check_god_module(_parse(funcs), Path("f.py"), "pkg")
    assert len(issues) == 1
    assert issues[0].rule == "god-module"


def test_check_god_module_clean():
    funcs = "\n".join(f"def func{i}() -> None: pass" for i in range(10))
    issues = ds.check_god_module(_parse(funcs), Path("f.py"), "pkg")
    assert issues == []


# ── Mutable Default ─────────────────────────────────────────────────────────


def test_check_mutable_defaults_flags_list():
    source = "def func(x: list = []) -> None: pass\n"
    issues = ds.check_mutable_defaults(_parse(source), Path("f.py"), "pkg")
    assert len(issues) == 1
    assert issues[0].rule == "mutable-default"


def test_check_mutable_defaults_flags_dict():
    source = "def func(x: dict = {}) -> None: pass\n"
    issues = ds.check_mutable_defaults(_parse(source), Path("f.py"), "pkg")
    assert len(issues) == 1


def test_check_mutable_defaults_flags_set():
    source = "def func(x: set = set()) -> None: pass\n"
    issues = ds.check_mutable_defaults(_parse(source), Path("f.py"), "pkg")
    assert issues == []  # set() is a call, not a literal


def test_check_mutable_defaults_clean():
    source = "def func(x: list | None = None) -> None: pass\n"
    issues = ds.check_mutable_defaults(_parse(source), Path("f.py"), "pkg")
    assert issues == []


# ── Star Import ─────────────────────────────────────────────────────────────


def test_check_star_imports_flags():
    source = "from os import *\n"
    issues = ds.check_star_imports(_parse(source), Path("f.py"), "pkg")
    assert len(issues) == 1
    assert issues[0].rule == "star-import"


def test_check_star_imports_clean():
    source = "from os import path, sep\n"
    issues = ds.check_star_imports(_parse(source), Path("f.py"), "pkg")
    assert issues == []


# ── Global Mutations ─────────────────────────────────────────────────────────


def test_check_global_mutations_flags_list():
    source = "MY_LIST = [1, 2, 3]\n"
    issues = ds.check_global_mutations(_parse(source), Path("f.py"), "pkg")
    assert len(issues) == 1
    assert issues[0].rule == "global-mutable"


def test_check_global_mutations_flags_dict():
    source = "MY_DICT = {'a': 1}\n"
    issues = ds.check_global_mutations(_parse(source), Path("f.py"), "pkg")
    assert len(issues) == 1


def test_check_global_mutations_skips_private():
    source = "_MY_LIST = [1, 2, 3]\n"
    issues = ds.check_global_mutations(_parse(source), Path("f.py"), "pkg")
    assert issues == []


def test_check_global_mutations_skips_non_mutable():
    source = "MY_INT = 42\nMY_STR = 'hello'\n"
    issues = ds.check_global_mutations(_parse(source), Path("f.py"), "pkg")
    assert issues == []


# ── Scope Mutation (Shared Mutable State) ───────────────────────────────────


def test_check_scope_mutations_flags_global_augassign():
    source = "counter = 0\ndef increment():\n    global counter\n    counter += 1\n"
    issues = ds.check_scope_mutations(_parse(source), Path("f.py"), "pkg")
    assert len(issues) == 1
    assert issues[0].rule == "scope-mutation"
    assert "counter" in issues[0].message
    assert "global" in issues[0].message


def test_check_scope_mutations_flags_global_assign():
    source = "config = {}\ndef reload() -> None:\n    global config\n    config = load_config()\n"
    issues = ds.check_scope_mutations(_parse(source), Path("f.py"), "pkg")
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
    issues = ds.check_scope_mutations(_parse(source), Path("f.py"), "pkg")
    assert len(issues) == 1
    assert issues[0].rule == "scope-mutation"
    assert "nonlocal" in issues[0].message


def test_check_scope_mutations_skips_read_only():
    source = "counter = 0\ndef read() -> int:\n    global counter\n    return counter\n"
    issues = ds.check_scope_mutations(_parse(source), Path("f.py"), "pkg")
    assert issues == []


def test_check_scope_mutations_skips_local_variable():
    source = "def func() -> None:\n    x = 10\n    x = x + 1\n"
    issues = ds.check_scope_mutations(_parse(source), Path("f.py"), "pkg")
    assert issues == []


def test_check_scope_mutations_clean_when_no_global():
    source = "def func(a: int) -> int:\n    result = a * 2\n    return result\n"
    issues = ds.check_scope_mutations(_parse(source), Path("f.py"), "pkg")
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
    issues = ds.check_scope_mutations(_parse(source), Path("f.py"), "pkg")
    assert len(issues) == 1


# ── Syntax Error Handling ────────────────────────────────────────────────────


def test_scan_package_handles_syntax_error(tmp_path: Path):
    pkg_dir = tmp_path / "syntax_err_pkg"
    pkg_dir.mkdir()
    (pkg_dir / "broken.py").write_text("def f(\n")
    result = ds.scan_package(
        "syntax_err_pkg", pkg_dir, exclude=[], min_duplicate_lines=5, twin_similarity=0.6
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
        issues = ds.check_import_cycles_pkg(pkg_name, files)
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
        issues = ds.check_import_cycles_pkg(pkg_name, files)
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
        "suppress_twin_pkg", pkg_dir, exclude=[], min_duplicate_lines=5, twin_similarity=0.6
    )
    assert not any(i.rule == "sync-async-duplication" for i in result.issues)
    assert result.suppressed == 1


# ── Gap-analysis rules ──────────────────────────────────────────────────────
# Tests for the 7 new rules added following the PyExamine / smellcheck /
# DPy / code-quality-analyzer gap analysis.


# ── Rule: dead-code ─────────────────────────────────────────────────────────


def test_check_dead_code_flags_after_return():
    source = "def f():\n    return 1\n    x = 2\n"
    issues = ds.check_dead_code(_parse(source), Path("f.py"), "pkg")
    assert len(issues) == 1
    assert issues[0].rule == "dead-code"
    assert issues[0].severity == "warning"
    assert issues[0].line == 3


def test_check_dead_code_flags_after_raise():
    source = "def f():\n    raise ValueError('bad')\n    x = 2\n"
    issues = ds.check_dead_code(_parse(source), Path("f.py"), "pkg")
    assert len(issues) == 1
    assert issues[0].rule == "dead-code"


def test_check_dead_code_flags_after_break():
    source = "def f():\n    for i in range(10):\n        break\n        x = i\n"
    issues = ds.check_dead_code(_parse(source), Path("f.py"), "pkg")
    assert len(issues) == 1
    assert issues[0].rule == "dead-code"


def test_check_dead_code_flags_after_continue():
    source = "def f():\n    for i in range(10):\n        continue\n        x = i\n"
    issues = ds.check_dead_code(_parse(source), Path("f.py"), "pkg")
    assert len(issues) == 1
    assert issues[0].rule == "dead-code"


def test_check_dead_code_flags_multiple_lines_after_return():
    source = "def f():\n    return\n    x = 2\n    y = 3\n"
    issues = ds.check_dead_code(_parse(source), Path("f.py"), "pkg")
    assert len(issues) == 2
    assert all(i.rule == "dead-code" for i in issues)


def test_check_dead_code_no_flag_when_no_terminator():
    source = "def f():\n    x = 1\n    y = 2\n    return x + y\n"
    issues = ds.check_dead_code(_parse(source), Path("f.py"), "pkg")
    assert issues == []


def test_check_dead_code_flags_in_if_body():
    source = "def f():\n    if True:\n        return\n        x = 1\n"
    issues = ds.check_dead_code(_parse(source), Path("f.py"), "pkg")
    assert len(issues) == 1


def test_check_dead_code_no_flag_for_class_body():
    # Dead code detection only runs inside function bodies, not class bodies
    source = "class C:\n    return 1\n    x = 2\n"
    issues = ds.check_dead_code(_parse(source), Path("f.py"), "pkg")
    assert issues == []


# ── Rule: message-chain ─────────────────────────────────────────────────────


def test_check_message_chains_flags_deep_chain():
    source = "def f():\n    a.b().c().d().e()\n"
    issues = ds.check_message_chains(_parse(source), Path("f.py"), "pkg")
    assert len(issues) == 1
    assert issues[0].rule == "message-chain"
    assert issues[0].severity == "info"
    assert "depth 4" in issues[0].message


def test_check_message_chains_allows_short_chain():
    source = "def f():\n    a.b().c()\n"
    issues = ds.check_message_chains(_parse(source), Path("f.py"), "pkg")
    assert issues == []


def test_check_message_chains_flags_deep_attr_chain():
    source = "def f():\n    a.b.c.d.e\n"
    issues = ds.check_message_chains(_parse(source), Path("f.py"), "pkg")
    assert len(issues) == 1
    assert issues[0].rule == "message-chain"


def test_check_message_chains_allows_single_attr():
    source = "def f():\n    a.b\n"
    issues = ds.check_message_chains(_parse(source), Path("f.py"), "pkg")
    assert issues == []


# ── Rule: excessive-decorators ──────────────────────────────────────────────


def test_check_excessive_decorators_flags_many_decorators():
    source = "@d1\n@d2\n@d3\n@d4\ndef f():\n    pass\n"
    issues = ds.check_excessive_decorators(_parse(source), Path("f.py"), "pkg")
    assert len(issues) == 1
    assert issues[0].rule == "excessive-decorators"
    assert issues[0].severity == "info"
    assert "4 decorators" in issues[0].message


def test_check_excessive_decorators_allows_three_decorators():
    source = "@d1\n@d2\n@d3\ndef f():\n    pass\n"
    issues = ds.check_excessive_decorators(_parse(source), Path("f.py"), "pkg")
    assert issues == []


def test_check_excessive_decorators_flags_class():
    source = "@d1\n@d2\n@d3\n@d4\nclass C:\n    pass\n"
    issues = ds.check_excessive_decorators(_parse(source), Path("f.py"), "pkg")
    assert len(issues) == 1
    assert "class" in issues[0].message


def test_check_excessive_decorators_no_decorator():
    source = "def f():\n    pass\n"
    issues = ds.check_excessive_decorators(_parse(source), Path("f.py"), "pkg")
    assert issues == []


# ── Rule: magic-number ──────────────────────────────────────────────────────


def test_check_magic_numbers_flags_literal():
    source = "def f():\n    x = 42\n"
    issues = ds.check_magic_numbers(_parse(source), Path("f.py"), "pkg")
    assert len(issues) == 1
    assert issues[0].rule == "magic-number"
    assert issues[0].severity == "info"
    assert "42" in issues[0].message


def test_check_magic_numbers_allows_zero_one_minus_one():
    source = "def f():\n    a = 0\n    b = 1\n    c = -1\n"
    issues = ds.check_magic_numbers(_parse(source), Path("f.py"), "pkg")
    assert issues == []


def test_check_magic_numbers_skips_init():
    source = "class C:\n    def __init__(self):\n        self.x = 42\n"
    issues = ds.check_magic_numbers(_parse(source), Path("f.py"), "pkg")
    assert issues == []


def test_check_magic_numbers_flags_float():
    source = "def f():\n    ratio = 0.75\n"
    issues = ds.check_magic_numbers(_parse(source), Path("f.py"), "pkg")
    assert len(issues) == 1
    assert issues[0].rule == "magic-number"


def test_check_magic_numbers_no_flag_in_string():
    source = "def f():\n    x = '42'\n"
    issues = ds.check_magic_numbers(_parse(source), Path("f.py"), "pkg")
    assert issues == []


# ── Rule: missing-else ──────────────────────────────────────────────────────


def test_check_missing_else_flags_nontrivial_if():
    source = "def f():\n    if x:\n        a = 1\n        b = 2\n"
    issues = ds.check_missing_else(_parse(source), Path("f.py"), "pkg")
    assert len(issues) == 1
    assert issues[0].rule == "missing-else"
    assert issues[0].severity == "info"


def test_check_missing_else_allows_if_else():
    source = "def f():\n    if x:\n        a = 1\n        b = 2\n    else:\n        pass\n"
    issues = ds.check_missing_else(_parse(source), Path("f.py"), "pkg")
    assert issues == []


def test_check_missing_else_allows_single_statement_if():
    source = "def f():\n    if x:\n        a = 1\n"
    issues = ds.check_missing_else(_parse(source), Path("f.py"), "pkg")
    assert issues == []


def test_check_missing_else_allows_if_elif():
    source = "def f():\n    if x:\n        a = 1\n        b = 2\n    elif y:\n        pass\n"
    issues = ds.check_missing_else(_parse(source), Path("f.py"), "pkg")
    assert issues == []


def test_check_missing_else_allows_guard_clause_return():
    source = "def f():\n    if x:\n        a = 1\n        return\n    return a\n"
    assert ds.check_missing_else(_parse(source), Path("f.py"), "pkg") == []


def test_check_missing_else_allows_guard_clause_raise():
    source = "def f():\n    if x:\n        a = 1\n        raise ValueError(a)\n"
    assert ds.check_missing_else(_parse(source), Path("f.py"), "pkg") == []


def test_check_missing_else_allows_loop_skip_continue():
    source = "def f():\n    for x in y:\n        if x in seen:\n            a = 1\n            continue\n"
    assert ds.check_missing_else(_parse(source), Path("f.py"), "pkg") == []


def test_check_missing_else_allows_loop_skip_break():
    source = (
        "def f():\n    for x in y:\n        if x is done:\n            a = 1\n            break\n"
    )
    assert ds.check_missing_else(_parse(source), Path("f.py"), "pkg") == []


def test_check_missing_else_still_flags_non_terminated_if():
    # Sanity check: the terminator narrowing must not swallow genuine hits —
    # a 2+ statement if with no else/elif/terminator is still flagged.
    source = "def f():\n    if x:\n        a = 1\n        b = 2\n    return a + b\n"
    issues = ds.check_missing_else(_parse(source), Path("f.py"), "pkg")
    assert len(issues) == 1


# ── Rule: lazy-class ────────────────────────────────────────────────────────


def test_check_lazy_class_flags_zero_methods():
    source = "class C:\n    x = 1\n"
    issues = ds.check_lazy_class(_parse(source), Path("f.py"), "pkg")
    assert len(issues) == 1
    assert issues[0].rule == "lazy-class"
    assert issues[0].severity == "info"
    assert "0 method" in issues[0].message


def test_check_lazy_class_flags_one_method():
    source = "class C:\n    def f(self):\n        pass\n"
    issues = ds.check_lazy_class(_parse(source), Path("f.py"), "pkg")
    assert len(issues) == 1
    assert issues[0].rule == "lazy-class"
    assert "1 method" in issues[0].message


def test_check_lazy_class_allows_two_methods():
    source = "class C:\n    def f(self):\n        pass\n    def g(self):\n        pass\n"
    issues = ds.check_lazy_class(_parse(source), Path("f.py"), "pkg")
    assert issues == []


def test_check_lazy_class_allows_non_class():
    source = "def f():\n    pass\n"
    issues = ds.check_lazy_class(_parse(source), Path("f.py"), "pkg")
    assert issues == []


def test_check_lazy_class_allows_pydantic_base_model():
    source = "class C(BaseModel):\n    x: int = 1\n"
    issues = ds.check_lazy_class(_parse(source), Path("f.py"), "pkg")
    assert issues == []


def test_check_lazy_class_allows_pydantic_base_model_qualified():
    source = "class C(pydantic.BaseModel):\n    x: int = 1\n"
    issues = ds.check_lazy_class(_parse(source), Path("f.py"), "pkg")
    assert issues == []


def test_check_lazy_class_allows_pydantic_base_settings():
    source = "class C(BaseSettings):\n    x: int = 1\n"
    issues = ds.check_lazy_class(_parse(source), Path("f.py"), "pkg")
    assert issues == []


def test_check_lazy_class_allows_named_tuple():
    source = "class C(NamedTuple):\n    x: int\n"
    issues = ds.check_lazy_class(_parse(source), Path("f.py"), "pkg")
    assert issues == []


def test_check_lazy_class_allows_named_tuple_qualified():
    source = "class C(typing.NamedTuple):\n    x: int\n"
    issues = ds.check_lazy_class(_parse(source), Path("f.py"), "pkg")
    assert issues == []


def test_check_lazy_class_allows_dataclass_decorator():
    source = "@dataclass\nclass C:\n    x: int = 1\n"
    issues = ds.check_lazy_class(_parse(source), Path("f.py"), "pkg")
    assert issues == []


def test_check_lazy_class_allows_dataclass_decorator_with_args():
    source = "@dataclass(frozen=True)\nclass C:\n    x: int = 1\n"
    issues = ds.check_lazy_class(_parse(source), Path("f.py"), "pkg")
    assert issues == []


def test_check_lazy_class_still_flags_unrelated_base():
    # Sanity check: the pydantic/dataclass exemption must not swallow
    # genuine hits — an unrelated base class doesn't grant an exemption.
    source = "class C(SomeOtherBase):\n    x = 1\n"
    issues = ds.check_lazy_class(_parse(source), Path("f.py"), "pkg")
    assert len(issues) == 1


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
    issues = ds.check_deep_inheritance(_parse(source), Path("f.py"), "pkg")
    assert len(issues) >= 1
    assert issues[0].rule == "deep-inheritance"
    assert issues[0].severity == "warning"


def test_check_deep_inheritance_allows_shallow():
    source = "class A:\n    pass\nclass B(A):\n    pass\n"
    issues = ds.check_deep_inheritance(_parse(source), Path("f.py"), "pkg")
    assert issues == []


def test_check_deep_inheritance_no_bases():
    source = "class C:\n    pass\n"
    issues = ds.check_deep_inheritance(_parse(source), Path("f.py"), "pkg")
    assert issues == []


def test_check_deep_inheritance_allows_single_level():
    source = "class Base:\n    pass\nclass Child(Base):\n    pass\n"
    issues = ds.check_deep_inheritance(_parse(source), Path("f.py"), "pkg")
    assert issues == []


# ── Remediation Priority ────────────────────────────────────────────────────


def test_compute_priority_score_error_high_effort():
    issue = ds.Issue(
        file=Path("f.py"),
        line=1,
        severity="error",
        rule="import-cycle",
        message="circular import",
        package="pkg",
    )
    score = ds.compute_priority_score(issue)
    # error=6.0 × effort=5.0 = 30.0
    assert score == 30.0


def test_compute_priority_score_warning_moderate_effort():
    issue = ds.Issue(
        file=Path("f.py"),
        line=1,
        severity="warning",
        rule="long-function",
        message="too long",
        package="pkg",
    )
    score = ds.compute_priority_score(issue)
    # warning=1.5 × effort=2.5 = 3.75
    assert score == 3.75


def test_compute_priority_score_info_trivial():
    issue = ds.Issue(
        file=Path("f.py"),
        line=1,
        severity="info",
        rule="dead-code",
        message="unreachable",
        package="pkg",
    )
    score = ds.compute_priority_score(issue)
    # info=0.3 × effort=0.5 = 0.15
    assert score == 0.15


def test_build_remediation_plan_groups_by_rule():
    issues = [
        ds.Issue(Path("a.py"), 1, "error", "import-cycle", "cycle", "pkg"),
        ds.Issue(Path("b.py"), 2, "error", "import-cycle", "cycle", "pkg"),
        ds.Issue(Path("c.py"), 3, "warning", "long-function", "long", "pkg"),
    ]
    steps = ds.build_remediation_plan(issues)
    assert len(steps) == 2
    # import-cycle has higher score (30.0) than long-function (3.75)
    assert steps[0].rule == "import-cycle"
    assert steps[0].count == 2
    assert steps[1].rule == "long-function"
    assert steps[1].count == 1


def test_build_remediation_plan_priority_labels():
    issues = [
        ds.Issue(Path("a.py"), 1, "error", "god-class", "too big", "pkg"),
        ds.Issue(Path("b.py"), 2, "info", "todo-marker", "todo", "pkg"),
    ]
    steps = ds.build_remediation_plan(issues)
    assert steps[0].priority == "P0"  # error × effort=5.0 = 30.0
    assert steps[1].priority == "P3"  # info × effort=0.5 = 0.15


def test_plan_report_contains_all_sections():
    issues = [
        ds.Issue(Path("a.py"), 1, "error", "import-cycle", "cycle", "pkg"),
        ds.Issue(Path("b.py"), 2, "warning", "long-function", "long", "pkg"),
    ]
    report = ds.plan_report(issues)
    assert "REMEDIATION PLAN" in report
    assert "RECOMMENDED FIX ORDER" in report
    assert "import-cycle" in report
    assert "long-function" in report
    assert "P0" in report or "P1" in report
    assert "P2" in report or "P3" in report


def test_plan_report_empty_issues():
    report = ds.plan_report([])
    assert "All clean" in report


def test_plan_report_groups_files():
    issues = [
        ds.Issue(Path("a.py"), 1, "warning", "long-function", "long", "pkg"),
        ds.Issue(Path("a.py"), 2, "warning", "long-function", "long", "pkg"),
        ds.Issue(Path("b.py"), 3, "warning", "long-function", "long", "pkg"),
    ]
    steps = ds.build_remediation_plan(issues)
    assert len(steps) == 1
    assert steps[0].count == 3
    assert len(steps[0].files) == 2  # a.py and b.py


def test_plan_report_truncates_by_top():
    issues = [
        ds.Issue(Path(f"file{i}.py"), 1, "info", f"rule-{i}", "msg", "pkg") for i in range(30)
    ]
    report = ds.plan_report(issues, top=5)
    assert "and 25 more rules" in report
