"""Tests for spaghetti.cli: package registry resolution, cwd discovery, and main()."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from spaghetti import detector as ds
from spaghetti.cli import (
    _load_packages_from_config,
    _parse_package_args,
    discover_cwd_packages,
    resolve_packages,
)

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
    """End-to-end: --package points main() at an arbitrary directory,
    proving the CLI plumbing (not just resolve_packages() in isolation)
    actually drives a real scan."""
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


def test_main_version_flag_prints_version_and_exits_zero(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
):
    monkeypatch.setattr("sys.argv", ["spaghetti", "--version"])

    with pytest.raises(SystemExit) as exc_info:
        ds.main()

    assert exc_info.value.code == 0
    assert "spaghetti" in capsys.readouterr().out


def test_main_quiet_flag_suppresses_banner_but_keeps_summary(
    fake_package: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
):
    monkeypatch.setattr(ds, "PACKAGES", dict(ds.PACKAGES))  # restore after test
    monkeypatch.setattr(
        "sys.argv",
        ["spaghetti", "--package", f"fake_pkg={fake_package}", "--severity", "info", "--quiet"],
    )

    ds.main()

    out = capsys.readouterr().out
    assert "SPAGHETTI CODE DETECTION REPORT" not in out
    assert "Files scanned:" in out


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


# ── JSON report: schema v1, locations, fingerprints ─────────────────────────


def _run_cli_json(pkg_dir: Path) -> dict:
    import json
    import subprocess

    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "spaghetti.detector",
            "--package",
            f"{pkg_dir.name}={pkg_dir}",
            "--json",
        ],
        capture_output=True,
        text=True,
        cwd=pkg_dir.parent,
    )
    assert proc.returncode in (0, 1, 2), proc.stderr
    return json.loads(proc.stdout)


def test_json_report_validates_against_committed_schema(tmp_path: Path):
    import json

    import jsonschema

    pkg_dir = tmp_path / "pkg"
    pkg_dir.mkdir()
    (pkg_dir / "m.py").write_text(
        "def f(a, b):  # spaghetti-ignore[missing-return-type]: demo\n"
        "    try:\n        pass\n    except:\n        pass\n"
    )
    report = _run_cli_json(pkg_dir)
    schema = json.loads((Path(__file__).parents[1] / "schema" / "report.v1.json").read_text())
    jsonschema.Draft202012Validator(schema).validate(report)
    assert report["schema_version"] == 1
    assert report["packages"]["pkg"]["files"] == 1
    assert report["summary"]["grade"] in "ABCDF"
    assert report["ignored"][0]["reason"] == "demo"


# ── Phase 1 flags: settings, baseline, --diff-base, --fail-on, --format ─────


def _run_cli(cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "spaghetti.detector", *args],
        capture_output=True,
        text=True,
        cwd=cwd,
    )


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


@pytest.fixture
def project(tmp_path: Path) -> Path:
    """A git repo with a pyproject [tool.spaghetti] table and one messy package."""
    root = tmp_path / "proj"
    (root / "src" / "mylib" / "routes").mkdir(parents=True)
    (root / "pyproject.toml").write_text(
        "[tool.spaghetti]\n"
        'fail-on = "error"\n'
        'baseline = ".spaghetti-baseline.json"\n'
        'library-packages = ["mylib"]\n'
        "[tool.spaghetti.packages]\n"
        'mylib = "src/mylib"\n'
        "[tool.spaghetti.thresholds]\n"
        "max-func-params = 0\n"
        "[tool.spaghetti.rules]\n"
        'missing-param-type = "off"\n'
        'missing-return-type = "error"\n'
        "[tool.spaghetti.layers.mylib]\n"
        '"routes/" = ["mylib.db."]\n'
    )
    (root / "src" / "mylib" / "m.py").write_text(
        "def f(x):\n    a = 1\n    b = 2\n    c = 3\n    return a + b + c + x\n"
    )
    (root / "src" / "mylib" / "routes" / "r.py").write_text(
        "from fastapi import APIRouter\nfrom mylib.db.conn import C\n"
    )
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "t@example.com")
    _git(root, "config", "user.name", "t")
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "init")
    return root


def test_pyproject_settings_drive_the_scan(project: Path):
    proc = _run_cli(project, "--json")
    assert proc.returncode == 2, proc.stderr  # fail-on=error and errors exist
    report = json.loads(proc.stdout)
    rules = {(i["rule"], i["severity"]) for i in report["issues"]}
    assert ("missing-return-type", "error") in rules  # severity override
    assert not any(r == "missing-param-type" for r, _ in rules)  # rule off
    assert ("layer-violation", "error") in rules  # layers table
    assert ("transport-in-library", "error") in rules  # library-packages
    assert ("too-many-params", "warning") in rules  # threshold lowered to 0
    assert report["baseline"] is None and report["diff_base"] is None


def test_no_pyproject_flag_ignores_the_table(project: Path):
    proc = _run_cli(project, "--no-pyproject", "--package", "mylib=src/mylib", "--json")
    report = json.loads(proc.stdout)
    assert any(i["rule"] == "missing-param-type" for i in report["issues"])
    assert not any(i["rule"] == "layer-violation" for i in report["issues"])


def test_invalid_pyproject_is_an_error_not_a_silent_fallback(project: Path):
    (project / "pyproject.toml").write_text("[tool.spaghetti]\nfail-on = \n")
    proc = _run_cli(project, "--json")
    assert proc.returncode == 2
    assert "invalid TOML" in proc.stderr
    assert proc.stdout == ""


def test_unknown_threshold_key_is_reported(project: Path):
    pyproject = project / "pyproject.toml"
    pyproject.write_text(
        pyproject.read_text().replace("[tool.spaghetti]\n", "[tool.spaghetti]\nbogus = 1\n")
    )
    proc = _run_cli(project, "--json")
    assert proc.returncode == 2
    assert "unknown setting(s): bogus" in proc.stderr


def test_fail_on_policy_controls_exit_code(project: Path):
    assert _run_cli(project, "--fail-on", "never").returncode == 0
    assert _run_cli(project, "--fail-on", "error").returncode == 2
    # drop the error-level rules: only warnings/infos remain
    (project / "src" / "mylib" / "routes" / "r.py").unlink()
    (project / "pyproject.toml").write_text(
        "[tool.spaghetti]\n[tool.spaghetti.packages]\nmylib = 'src/mylib'\n"
    )
    assert _run_cli(project, "--fail-on", "error").returncode == 0
    assert _run_cli(project, "--fail-on", "warning").returncode == 1
    assert _run_cli(project).returncode == 1  # default policy is warning


def test_baseline_roundtrip_hides_known_and_reports_fixed(project: Path):
    write = _run_cli(project, "--update-baseline")
    assert write.returncode == 0, write.stderr
    baseline_path = project / ".spaghetti-baseline.json"
    assert baseline_path.exists()
    n = len(json.loads(baseline_path.read_text())["findings"])
    assert n >= 5

    gated = _run_cli(project, "--json")
    assert gated.returncode == 0  # everything is baselined
    report = json.loads(gated.stdout)
    assert report["issues"] == []
    assert report["baseline"]["known"] == n
    assert report["baseline"]["fixed"] == []
    assert report["summary"]["issues"] == n  # summary still counts the full picture

    # Fix one finding and add a new one: the new one shows, the fixed one is listed.
    (project / "src" / "mylib" / "routes" / "r.py").write_text("from mylib.db.conn import C\n")
    (project / "src" / "mylib" / "m.py").write_text("def g(y):\n    return y\n")
    after = json.loads(_run_cli(project, "--json").stdout)
    assert {i["rule"] for i in after["issues"]} == {"missing-return-type", "too-many-params"}
    assert {e["rule"] for e in after["baseline"]["fixed"]} >= {"transport-in-library"}


def test_update_baseline_without_a_path_is_an_error(project: Path):
    (project / "pyproject.toml").write_text(
        "[tool.spaghetti]\n[tool.spaghetti.packages]\nmylib = 'src/mylib'\n"
    )
    proc = _run_cli(project, "--update-baseline")
    assert proc.returncode == 2
    assert "--update-baseline needs" in proc.stderr


def test_diff_base_restricts_to_changed_lines(project: Path):
    with (project / "src" / "mylib" / "m.py").open("a") as fh:
        fh.write("\n\ndef g(y):\n    return y\n")
    proc = _run_cli(project, "--diff-base", "HEAD", "--json", "--fail-on", "never")
    report = json.loads(proc.stdout)
    assert {i["rule"] for i in report["issues"]} == {"missing-return-type", "too-many-params"}
    assert all(i["message"].startswith("g()") for i in report["issues"])
    assert report["diff_base"]["ref"] == "HEAD"
    assert report["diff_base"]["hidden"] >= 5
    assert "Changed lines only (vs HEAD)" in _run_cli(project, "--diff-base", "HEAD", "-q").stdout


def test_diff_base_outside_a_repo_is_an_error(tmp_path: Path):
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "m.py").write_text("x = 1\n")
    proc = _run_cli(tmp_path, "--package", "pkg=pkg", "--diff-base", "HEAD")
    assert proc.returncode == 2
    assert "--diff-base" in proc.stderr


def test_sarif_output_is_well_formed(project: Path):
    _run_cli(project, "--update-baseline")
    (project / "src" / "mylib" / "m.py").write_text(
        "def g(y):  # spaghetti-ignore[missing-return-type]: demo\n    return y\n"
    )
    proc = _run_cli(project, "--format", "sarif", "--fail-on", "never")
    assert proc.returncode == 0, proc.stderr
    sarif = json.loads(proc.stdout)
    assert sarif["version"] == "2.1.0"
    run = sarif["runs"][0]
    assert run["tool"]["driver"]["name"] == "spaghetti"
    states = {r.get("baselineState") for r in run["results"]}
    assert "unchanged" in states
    suppressed = [r for r in run["results"] if "suppressions" in r]
    assert suppressed and suppressed[0]["suppressions"][0]["justification"] == "demo"
    uris = {
        r["locations"][0]["physicalLocation"]["artifactLocation"]["uri"] for r in run["results"]
    }
    assert all(not u.startswith("/") for u in uris)
    assert "src/mylib/m.py" in uris
