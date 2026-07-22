"""Package-wide configuration: workspace root, thresholds, layer rules, and the
mutable package registry used by every check function and the CLI.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

# ── Workspace root discovery ──────────────────────────────────────────────────


def _find_workspace_root(start: Path) -> Path | None:
    """Walk upward from ``start`` for the ``pyproject.toml`` declaring the uv workspace.

    Returns ``None`` rather than raising when no such ancestor exists —
    spaghetti is pip-installable and importable standalone (e.g. in this
    package's own standalone CI checkout, which has no sibling ``boti``/etc.
    directories and no ambient workspace at all), and a bare import must not
    crash. Callers fall back to an empty default package registry in that
    case; the CLI already requires an explicit ``--config``/``--package`` or
    errors out when the resolved registry is empty.
    """
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
    return None


WORKSPACE_ROOT: Path | None = _find_workspace_root(Path(__file__).resolve().parent)

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
DEFAULT_TOP_FILES = 5
DEFAULT_PLAN_TOP = 20
MIN_TWIN_FUNCTION_LINES = 4
MAX_PUBLIC_SYMBOLS = 15
MIN_CLASS_METHODS = 2

# Weighted Methods per Class: sum of each method's own cyclomatic complexity.
# A class can stay under MAX_CLASS_METHODS/MAX_CLASS_ATTRS yet still be a god
# class if its few methods are individually complex enough.
MAX_CLASS_WMC = 50

# LCOM4: number of connected components in the graph where two methods are
# linked if they share a self.<attr> reference. 1 means fully cohesive: every
# method connects to every other through shared state, directly or
# transitively. Anything higher is a genuine split into unrelated clusters.
MAX_CLASS_LCOM4 = 1
MIN_METHODS_FOR_COHESION = 3

# A class where fewer than this fraction of its methods reference any
# self.<attr> at all is treated as "essentially stateless" and skipped
# entirely — common for Strategy/Handler-pattern implementations (little to
# no shared instance state by design). A hard "zero state" cutoff misses
# classes that are almost entirely stateless but for a method or two
# referencing a sibling method as a bare callable (e.g.
# asyncio.to_thread(self.other_method, ...)) rather than genuine field
# access; a fractional threshold catches those too without hand-listing
# every such idiom.
MIN_STATEFUL_METHOD_FRACTION = 0.2

# A module is only flagged as an overloaded "hub" when it's both heavily
# depended-on (fan-in) and heavily dependent (fan-out) — either alone is
# often just a legitimately central util or a legitimately thin orchestrator.
MAX_MODULE_FAN_IN = 8
MAX_MODULE_FAN_OUT = 8

# By how much a warning-level threshold is multiplied to decide when a rule
# escalates its own finding to "error" instead (e.g. high-complexity,
# god-class) — one shared factor instead of each rule picking its own.
ERROR_ESCALATION_MULTIPLIER = 1.5

# Gap-analysis thresholds
MAX_MESSAGE_CHAIN_DEPTH = 3
MAX_DECORATORS = 3
MAX_INHERITANCE_DEPTH = 4

# ── Units & display ──────────────────────────────────────────────────────────

LINES_PER_KLOC = 1000
BANNER_WIDTH = 72

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
SUPPRESS_MARKER_RE = re.compile(r"#\s*spaghetti-ignore(?:\[([^\]]*)\])?(?:\s*:\s*(.*))?")
