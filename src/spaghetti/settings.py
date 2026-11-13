"""Project settings: ``[tool.spaghetti]`` in ``pyproject.toml`` and/or the
``--config`` YAML file.

Precedence, lowest to highest: built-in defaults in :mod:`spaghetti.config`,
``pyproject.toml`` found by walking up from the working directory, the
``--config`` YAML, explicit CLI flags.

Recognised keys (kebab-case in the file)::

    [tool.spaghetti]
    exclude = ["tests/", "migrations/"]
    fail-on = "error"                       # error | warning | info | never
    baseline = ".spaghetti-baseline.json"   # relative to the settings file
    snapshot-store = ".spaghetti/snapshots.jsonl"  # relative to the settings file
    cache = true                            # incremental per-file cache (--cache)
    cache-dir = ".spaghetti/cache"          # relative to the settings file

    [[tool.spaghetti.contracts]]            # architecture contracts, see spaghetti.contracts
    name = "domain stays framework-free"
    type = "forbidden"
    source = ["my_lib.domain"]
    forbidden = ["my_lib.transport", "fastapi"]
    library-packages = ["my-lib"]           # packages transport-in-library applies to

    [tool.spaghetti.packages]
    my-lib = "src/my_lib"                   # same mapping --config's `packages:` takes

    [tool.spaghetti.thresholds]             # any numeric constant in spaghetti.config
    max-function-lines = 60
    complexity-threshold = 12

    [tool.spaghetti.rules]                  # off, or a severity override
    magic-number = "off"
    missing-return-type = "info"

    [tool.spaghetti.layers.my-lib]          # path prefix -> forbidden import prefixes
    "routes/" = ["my_lib.db."]

Settings are applied by mutating :mod:`spaghetti.config` in place, because
every check reads its thresholds from there at call time. Worker processes
receive the same overrides through :func:`to_overrides` / :func:`apply_overrides`.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

import spaghetti.config as _cfg
from spaghetti.contracts import Contract, ContractError, parse_contracts
from spaghetti.rules import RULE_IDS

FAIL_ON_CHOICES = ("error", "warning", "info", "never")
RULE_SETTING_CHOICES = ("off", "info", "warning", "error")

_DISPLAY_CONSTANTS = frozenset({"BANNER_WIDTH", "LINES_PER_KLOC"})


def threshold_names() -> frozenset[str]:
    """Every tunable numeric constant in :mod:`spaghetti.config`."""
    return frozenset(
        name
        for name, value in vars(_cfg).items()
        if name.isupper()
        and isinstance(value, (int, float))
        and not isinstance(value, bool)
        and name not in _DISPLAY_CONSTANTS
    )


class SettingsError(ValueError):
    """A settings file is malformed; the message names the file and key."""


@dataclass
class Settings:
    thresholds: dict[str, int | float] = field(default_factory=dict)
    rules: dict[str, str] = field(default_factory=dict)
    layers: dict[str, dict[str, list[str]]] = field(default_factory=dict)
    library_packages: list[str] = field(default_factory=list)
    exclude: list[str] = field(default_factory=list)
    fail_on: str | None = None
    baseline: Path | None = None
    snapshot_store: Path | None = None
    cache: bool | None = None
    cache_dir: Path | None = None
    contracts: list[Contract] = field(default_factory=list)
    packages: dict[str, Path] = field(default_factory=dict)
    sources: list[Path] = field(default_factory=list)

    def merged_with(self, other: Settings) -> Settings:
        """``other`` wins on scalar conflicts; mappings and lists combine."""
        return Settings(
            thresholds={**self.thresholds, **other.thresholds},
            rules={**self.rules, **other.rules},
            layers={**self.layers, **other.layers},
            library_packages=[*self.library_packages, *other.library_packages],
            exclude=[*self.exclude, *other.exclude],
            fail_on=other.fail_on if other.fail_on is not None else self.fail_on,
            baseline=other.baseline if other.baseline is not None else self.baseline,
            snapshot_store=(
                other.snapshot_store if other.snapshot_store is not None else self.snapshot_store
            ),
            cache=other.cache if other.cache is not None else self.cache,
            cache_dir=other.cache_dir if other.cache_dir is not None else self.cache_dir,
            contracts=[*self.contracts, *other.contracts],
            packages={**self.packages, **other.packages},
            sources=[*self.sources, *other.sources],
        )


# ── Discovery & loading ───────────────────────────────────────────────────────


def find_pyproject(start: Path) -> Path | None:
    """Nearest ``pyproject.toml`` at or above ``start`` with a ``[tool.spaghetti]`` table.

    A ``pyproject.toml`` that fails to parse raises :class:`SettingsError`
    rather than being skipped: silently falling back to defaults would turn a
    typo in the settings into a run that ignores every setting.
    """
    for candidate in (start, *start.parents):
        pyproject = candidate / "pyproject.toml"
        if not pyproject.is_file():
            continue
        try:
            data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
        except tomllib.TOMLDecodeError as exc:
            raise SettingsError(f"{pyproject}: invalid TOML — {exc}") from exc
        except OSError:
            continue
        if "spaghetti" in data.get("tool", {}):
            return pyproject
    return None


def load_settings(*, pyproject: Path | None, yaml_path: Path | None) -> Settings:
    settings = Settings()
    if pyproject is not None:
        data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
        settings = settings.merged_with(
            parse_mapping(data.get("tool", {}).get("spaghetti", {}), origin=pyproject)
        )
    if yaml_path is not None:
        raw = yaml.safe_load(yaml_path.read_text(encoding="utf-8")) or {}
        if not isinstance(raw, dict):
            raise SettingsError(f"{yaml_path}: top level must be a mapping")
        from_yaml = parse_mapping(raw, origin=yaml_path)
        settings = settings.merged_with(from_yaml)
        if from_yaml.packages:
            # --config's ``packages:`` is the full registry, not an overlay.
            settings.packages = dict(from_yaml.packages)
    return settings


_SCALAR_KEYS = {
    "exclude",
    "fail-on",
    "baseline",
    "snapshot-store",
    "cache",
    "cache-dir",
    "library-packages",
    "packages",
    "contracts",
}
_TABLE_KEYS = {"thresholds", "rules", "layers"}


def parse_mapping(data: dict[str, Any], *, origin: Path) -> Settings:
    """Validate one settings mapping. Relative paths resolve against ``origin``'s directory."""
    base_dir = origin.parent
    unknown = set(data) - _SCALAR_KEYS - _TABLE_KEYS
    if unknown:
        raise SettingsError(
            f"{origin}: unknown setting(s): {', '.join(sorted(unknown))}. "
            f"Known: {', '.join(sorted(_SCALAR_KEYS | _TABLE_KEYS))}"
        )
    s = Settings(sources=[origin])
    s.exclude = _str_list(data.get("exclude", []), origin, "exclude")
    s.library_packages = _str_list(data.get("library-packages", []), origin, "library-packages")
    if (fail_on := data.get("fail-on")) is not None:
        if fail_on not in FAIL_ON_CHOICES:
            raise SettingsError(f"{origin}: fail-on must be one of {', '.join(FAIL_ON_CHOICES)}")
        s.fail_on = fail_on
    if (cache := data.get("cache")) is not None:
        if not isinstance(cache, bool):
            raise SettingsError(f"{origin}: cache must be true or false")
        s.cache = cache
    s.baseline = _path_setting(data, "baseline", origin)
    s.cache_dir = _path_setting(data, "cache-dir", origin)
    s.snapshot_store = _path_setting(data, "snapshot-store", origin)
    packages = data.get("packages", {})
    if not isinstance(packages, dict) or not all(isinstance(v, str) for v in packages.values()):
        raise SettingsError(f"{origin}: packages must map names to path strings")
    s.packages = {name: (base_dir / path).resolve() for name, path in packages.items()}
    try:
        s.contracts = parse_contracts(data.get("contracts", []), str(origin))
    except ContractError as exc:
        raise SettingsError(str(exc)) from exc
    s.thresholds = _parse_thresholds(data.get("thresholds", {}), origin)
    s.rules = _parse_rules(data.get("rules", {}), origin)
    s.layers = _parse_layers(data.get("layers", {}), origin)
    return s


def _path_setting(data: dict[str, Any], key: str, origin: Path) -> Path | None:
    """An optional path-valued key, resolved against the settings file's directory."""
    value = data.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise SettingsError(f"{origin}: {key} must be a path string")
    return (origin.parent / value).resolve()


def _str_list(value: Any, origin: Path, key: str) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise SettingsError(f"{origin}: {key} must be a list of strings")
    return list(value)


def _parse_thresholds(table: Any, origin: Path) -> dict[str, int | float]:
    if not isinstance(table, dict):
        raise SettingsError(f"{origin}: thresholds must be a table")
    valid = threshold_names()
    out: dict[str, int | float] = {}
    for key, value in table.items():
        name = key.upper().replace("-", "_")
        if name not in valid:
            raise SettingsError(
                f"{origin}: unknown threshold '{key}'. Known: "
                + ", ".join(sorted(n.lower().replace("_", "-") for n in valid))
            )
        current = getattr(_cfg, name)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise SettingsError(f"{origin}: threshold '{key}' must be a number")
        if isinstance(current, int) and not isinstance(value, int):
            raise SettingsError(f"{origin}: threshold '{key}' must be an integer")
        out[name] = value
    return out


def _parse_rules(table: Any, origin: Path) -> dict[str, str]:
    if not isinstance(table, dict):
        raise SettingsError(f"{origin}: rules must be a table")
    out: dict[str, str] = {}
    for rule, value in table.items():
        if rule not in RULE_IDS:
            raise SettingsError(f"{origin}: unknown rule '{rule}'")
        if value not in RULE_SETTING_CHOICES:
            raise SettingsError(
                f"{origin}: rule '{rule}' must be one of {', '.join(RULE_SETTING_CHOICES)}"
            )
        out[rule] = value
    return out


def _parse_layers(table: Any, origin: Path) -> dict[str, dict[str, list[str]]]:
    if not isinstance(table, dict):
        raise SettingsError(f"{origin}: layers must be a table")
    out: dict[str, dict[str, list[str]]] = {}
    for pkg, rules in table.items():
        if not isinstance(rules, dict):
            raise SettingsError(f"{origin}: layers.{pkg} must map path prefixes to lists")
        out[pkg] = {
            prefix: _str_list(forbidden, origin, f"layers.{pkg}.{prefix}")
            for prefix, forbidden in rules.items()
        }
    return out


# ── Application ───────────────────────────────────────────────────────────────


def to_overrides(settings: Settings) -> dict[str, Any]:
    """The picklable subset a worker process needs to reproduce ``apply_settings``."""
    return {
        "thresholds": dict(settings.thresholds),
        "rules": dict(settings.rules),
        "layers": settings.layers,
        "library_packages": list(settings.library_packages),
        "contracts": [c.to_json() for c in settings.contracts],
    }


def apply_overrides(overrides: dict[str, Any]) -> None:
    for name, value in overrides.get("thresholds", {}).items():
        setattr(_cfg, name, value)
    _cfg.RULE_OVERRIDES = dict(overrides.get("rules", {}))
    _cfg.LAYER_RULES = overrides.get("layers", {})
    _cfg.LIBRARY_PACKAGES = frozenset(overrides.get("library_packages", []))
    _cfg.CONTRACTS = [Contract.from_json(c) for c in overrides.get("contracts", [])]


def apply_settings(settings: Settings) -> dict[str, Any]:
    """Mutate :mod:`spaghetti.config` to reflect ``settings``; returns the overrides."""
    overrides = to_overrides(settings)
    apply_overrides(overrides)
    return overrides
