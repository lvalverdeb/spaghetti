"""Tests for spaghetti.cli: package registry resolution, cwd discovery, and main()."""

from __future__ import annotations

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
