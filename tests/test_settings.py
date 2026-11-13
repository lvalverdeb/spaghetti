"""Tests for spaghetti.settings: discovery, parsing, validation, application."""

from __future__ import annotations

from pathlib import Path

import pytest

import spaghetti.config as _cfg
from spaghetti.settings import (
    Settings,
    SettingsError,
    apply_overrides,
    apply_settings,
    find_pyproject,
    load_settings,
    parse_mapping,
    threshold_names,
    to_overrides,
)


@pytest.fixture(autouse=True)
def _restore_config(monkeypatch: pytest.MonkeyPatch):
    """Every test mutates spaghetti.config through apply_*; put it back."""
    snapshot = {name: getattr(_cfg, name) for name in threshold_names()}
    snapshot.update(
        LAYER_RULES=_cfg.LAYER_RULES,
        RULE_OVERRIDES=_cfg.RULE_OVERRIDES,
        LIBRARY_PACKAGES=_cfg.LIBRARY_PACKAGES,
    )
    yield
    for name, value in snapshot.items():
        setattr(_cfg, name, value)


def _write_pyproject(dir_: Path, body: str) -> Path:
    path = dir_ / "pyproject.toml"
    path.write_text(f"[tool.spaghetti]\n{body}", encoding="utf-8")
    return path


# ── Discovery ────────────────────────────────────────────────────────────────


def test_find_pyproject_walks_up_and_requires_tool_table(tmp_path: Path):
    (tmp_path / "pyproject.toml").write_text("[tool.spaghetti]\nexclude = []\n")
    nested = tmp_path / "a" / "b"
    nested.mkdir(parents=True)
    (tmp_path / "a" / "pyproject.toml").write_text("[project]\nname = 'x'\n")
    assert find_pyproject(nested) == tmp_path / "pyproject.toml"


def test_find_pyproject_none_when_absent(tmp_path: Path):
    assert find_pyproject(tmp_path) is None


def test_find_pyproject_refuses_to_skip_invalid_toml(tmp_path: Path):
    # A broken pyproject must not silently turn into "no settings".
    (tmp_path / "pyproject.toml").write_text("[tool.spaghetti]\nx = \n")
    with pytest.raises(SettingsError, match="invalid TOML"):
        find_pyproject(tmp_path)


# ── Parsing & validation ─────────────────────────────────────────────────────


def test_parse_mapping_full_example(tmp_path: Path):
    origin = tmp_path / "pyproject.toml"
    s = parse_mapping(
        {
            "exclude": ["tests/"],
            "fail-on": "error",
            "baseline": ".baseline.json",
            "library-packages": ["lib"],
            "packages": {"lib": "src/lib"},
            "thresholds": {"max-function-lines": 80, "default-twin-similarity": 0.7},
            "rules": {"magic-number": "off", "missing-return-type": "error"},
            "layers": {"lib": {"routes/": ["lib.db."]}},
        },
        origin=origin,
    )
    assert s.exclude == ["tests/"]
    assert s.fail_on == "error"
    assert s.baseline == (tmp_path / ".baseline.json").resolve()
    assert s.library_packages == ["lib"]
    assert s.packages == {"lib": (tmp_path / "src/lib").resolve()}
    assert s.thresholds == {"MAX_FUNCTION_LINES": 80, "DEFAULT_TWIN_SIMILARITY": 0.7}
    assert s.rules == {"magic-number": "off", "missing-return-type": "error"}
    assert s.layers == {"lib": {"routes/": ["lib.db."]}}
    assert s.sources == [origin]


@pytest.mark.parametrize(
    ("data", "match"),
    [
        ({"bogus": 1}, "unknown setting"),
        ({"fail-on": "sometimes"}, "fail-on must be one of"),
        ({"thresholds": {"bogus": 1}}, "unknown threshold 'bogus'"),
        ({"thresholds": {"max-function-lines": "50"}}, "must be a number"),
        ({"thresholds": {"max-function-lines": 50.5}}, "must be an integer"),
        ({"thresholds": {"max-function-lines": True}}, "must be a number"),
        ({"rules": {"not-a-rule": "off"}}, "unknown rule"),
        ({"rules": {"magic-number": "maybe"}}, "must be one of"),
        ({"layers": {"lib": ["x"]}}, "must map path prefixes"),
        ({"exclude": "tests/"}, "list of strings"),
        ({"packages": {"lib": 3}}, "path strings"),
    ],
)
def test_parse_mapping_rejects_bad_input(tmp_path: Path, data: dict, match: str):
    with pytest.raises(SettingsError, match=match):
        parse_mapping(data, origin=tmp_path / "pyproject.toml")


def test_threshold_names_cover_the_tunables_and_exclude_display_constants():
    names = threshold_names()
    assert {"MAX_FUNCTION_LINES", "COMPLEXITY_THRESHOLD", "MAX_CLASS_LCOM4"} <= names
    assert "BANNER_WIDTH" not in names
    assert "LINES_PER_KLOC" not in names


# ── Merging pyproject + --config ─────────────────────────────────────────────


def test_load_settings_yaml_overrides_pyproject_and_replaces_packages(tmp_path: Path):
    pyproject = _write_pyproject(
        tmp_path,
        'fail-on = "warning"\nexclude = ["a/"]\n'
        "[tool.spaghetti.packages]\nold = 'src/old'\n"
        "[tool.spaghetti.thresholds]\nmax-function-lines = 60\n",
    )
    cfg_dir = tmp_path / "cfg"
    cfg_dir.mkdir()
    yaml_path = cfg_dir / "spaghetti.yaml"
    yaml_path.write_text(
        "fail-on: error\nexclude: [b/]\npackages:\n  new: pkg\nthresholds:\n  max-returns: 5\n"
    )
    s = load_settings(pyproject=pyproject, yaml_path=yaml_path)
    assert s.fail_on == "error"
    assert s.exclude == ["a/", "b/"]
    assert s.thresholds == {"MAX_FUNCTION_LINES": 60, "MAX_RETURNS": 5}
    # --config's packages are the whole registry, resolved against its own directory
    assert s.packages == {"new": (cfg_dir / "pkg").resolve()}


def test_load_settings_with_nothing_is_empty():
    assert load_settings(pyproject=None, yaml_path=None) == Settings()


# ── Application ──────────────────────────────────────────────────────────────


def test_apply_settings_mutates_config_and_roundtrips_through_overrides():
    s = Settings(
        thresholds={"MAX_FUNCTION_LINES": 7},
        rules={"magic-number": "off"},
        layers={"lib": {"": ["x"]}},
        library_packages=["lib"],
    )
    overrides = apply_settings(s)
    assert _cfg.MAX_FUNCTION_LINES == 7
    assert _cfg.RULE_OVERRIDES == {"magic-number": "off"}
    assert _cfg.LAYER_RULES == {"lib": {"": ["x"]}}
    assert _cfg.LIBRARY_PACKAGES == frozenset({"lib"})
    # What a worker process receives must reproduce the same state.
    _cfg.MAX_FUNCTION_LINES = 50
    _cfg.RULE_OVERRIDES = {}
    apply_overrides(overrides)
    assert _cfg.MAX_FUNCTION_LINES == 7
    assert _cfg.RULE_OVERRIDES == {"magic-number": "off"}
    assert to_overrides(s) == overrides
