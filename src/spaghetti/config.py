"""Package-wide configuration: workspace root, thresholds, layer rules, and the
mutable package registry used by every check function and the CLI.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

# ── Workspace root discovery ──────────────────────────────────────────────────


def _find_workspace_root(start: Path) -> Path:
    """Walk upward from ``start`` for the ``pyproject.toml`` declaring the uv workspace."""
    for candidate in (start, *start.parents):
        pyproject = candidate / "pyproject.toml"
        if not pyproject.is_file():
            continue
        try:
            data = tomllib.loads(pyproject.read_text())
        except (tomllib.TOMLDecodeError, OSError):
            continue
        if "workspace" in data.get("tool", {}).get("uv", {}):
            return candidate
    raise RuntimeError(
        f"Could not locate the workspace root (a pyproject.toml with "
        f"[tool.uv.workspace]) above {start}."
    )


WORKSPACE_ROOT = _find_workspace_root(Path(__file__).resolve().parent)

# ── Package registry ──────────────────────────────────────────────────────────

DEFAULT_PACKAGES: dict[str, Path] = {
    "boti": WORKSPACE_ROOT / "boti" / "src" / "boti",
    "boti-data": WORKSPACE_ROOT / "boti-data" / "src" / "boti_data",
    "boti-dask": WORKSPACE_ROOT / "boti-dask" / "src" / "boti_dask",
    "spaghetti": WORKSPACE_ROOT / "spaghetti" / "src" / "spaghetti",
}

PACKAGES: dict[str, Path] = dict(DEFAULT_PACKAGES)

# ── Thresholds ────────────────────────────────────────────────────────────────

MAX_FUNCTION_LINES = 50
MAX_FILE_LINES = 400
MAX_FUNC_PARAMS = 6
MAX_RETURNS = 3
MAX_NESTING_DEPTH = 5
MAX_CROSS_LAYER_IMPORTS = 0
COMPLEXITY_THRESHOLD = 10
MAX_CLASS_METHODS = 25
MAX_CLASS_ATTRS = 20
MIN_BOOLEAN_FLAGS = 3
DEFAULT_MIN_DUPLICATE_LINES = 5
DEFAULT_TWIN_SIMILARITY = 0.6

# Gap-analysis thresholds
MAX_MESSAGE_CHAIN_DEPTH = 3
MAX_DECORATORS = 3
MAX_INHERITANCE_DEPTH = 4

# ── Layer rules ───────────────────────────────────────────────────────────────

LAYER_RULES: dict[str, dict[str, list[str]]] = {
    "etl-demo": {
        "routes/": ["boti_data.", "boti.", "boti_dask."],
    },
    "etl-core": {
        "": ["fastapi", "starlette", "httpx"],
    },
}

ALLOWED_IMPORT_PREFIXES: dict[str, list[str]] = {
    "etl-core": ["etl_core."],
    "etl-demo": ["etl_demo."],
    "boti-data": ["boti_data."],
    "boti-dask": ["boti_dask."],
    "boti": ["boti."],
}

# ── Compiled regexes ──────────────────────────────────────────────────────────

DUNDER_RE = re.compile(r"^__.*__$")
TODO_RE = re.compile(r"#.*\b(TODO|FIXME|XXX|HACK)\b")
SUPPRESS_MARKER_RE = re.compile(r"#\s*spaghetti-ignore(?:\[([^\]]*)\])?")
