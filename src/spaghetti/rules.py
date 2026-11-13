"""The rule catalog: one entry per rule id the checks can emit.

This is the single source of truth for rule metadata consumed by SARIF output,
settings validation (``[tool.spaghetti.rules]``) and, later, generated docs.
Thresholds live in :mod:`spaghetti.config`; this module only describes.

``enabled_by_default=False`` marks rules that exist and can be switched on via
``[tool.spaghetti.rules]`` but ship off: the 2026-11 precision audit over five
public codebases (see ``docs/precision.md``) found them at or near zero
precision, or Ruff already owns them (``unused-import``, ``star-import``).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from spaghetti.models import Severity

Tier = Literal["ast", "text", "infra", "package", "workspace"]


@dataclass(frozen=True)
class RuleMeta:
    id: str
    summary: str
    default_severity: Severity
    tier: Tier
    enabled_by_default: bool = True


def _r(
    id: str,
    summary: str,
    default_severity: Severity,
    tier: Tier = "ast",
    *,
    enabled_by_default: bool = True,
) -> RuleMeta:
    return RuleMeta(id, summary, default_severity, tier, enabled_by_default)


_RULES: tuple[RuleMeta, ...] = (
    # ── per-file AST ──
    _r("long-function", "Function exceeds the line limit", "warning"),
    _r("high-complexity", "Cyclomatic complexity (McCabe, Radon-exact) above threshold", "warning"),
    _r("high-cognitive-complexity", "Cognitive complexity (Sonar) above threshold", "warning"),
    _r("low-maintainability", "Maintainability Index below threshold", "warning", "text"),
    _r("missing-return-type", "Public function without a return type annotation", "warning"),
    _r("missing-param-type", "Public function parameter without a type annotation", "info"),
    _r("too-many-params", "Function with too many parameters", "warning"),
    _r("excessive-returns", "Function with too many return statements", "info"),
    _r("boolean-flag-params", "Function with several boolean flag parameters", "info"),
    _r("deep-nesting", "Control flow nested too deeply", "warning"),
    _r("untyped-dict", "Bare dict in a type hint", "info"),
    _r("unused-import", "Imported name never referenced", "warning", enabled_by_default=False),
    _r("swallowed-exception", "except block that silently discards the exception", "warning"),
    _r("duplicate-branch", "if/else branches with identical bodies", "warning"),
    _r("encapsulation-violation", "Private attribute accessed from outside its owner", "info"),
    _r("god-class", "Class with too many methods, attributes, or total complexity", "warning"),
    _r("layer-violation", "Import forbidden by the configured layer rules", "error"),
    _r("transport-in-library", "Library package imports a transport framework", "error"),
    _r(
        "potential-circular-import",
        "Child module imports its parent package",
        "warning",
        enabled_by_default=False,
    ),
    _r("god-module", "Module exposes too many public symbols", "warning"),
    _r("mutable-default", "Mutable default argument", "warning"),
    _r("bare-except", "except: without an exception type", "warning"),
    _r("star-import", "from x import *", "warning", enabled_by_default=False),
    _r("global-mutable", "Mutable state assigned at module scope", "info"),
    _r("scope-mutation", "Function mutates outer scope via global/nonlocal", "info"),
    _r("dead-code", "Statement unreachable after return/raise/break/continue", "warning"),
    _r("message-chain", "Attribute or call chain deeper than the limit", "info"),
    _r("excessive-decorators", "Too many stacked decorators", "info"),
    _r("magic-number", "Unexplained numeric literal", "info", enabled_by_default=False),
    _r(
        "magic-string",
        "Same string literal compared for equality in several places",
        "info",
        enabled_by_default=False,
    ),
    _r("missing-else", "Multi-statement if without else", "info", enabled_by_default=False),
    _r("lazy-class", "Class with fewer than two methods", "info"),
    _r("deep-inheritance", "Inheritance chain deeper than the limit", "warning"),
    _r(
        "pass-through-method",
        "Method whose body only delegates with unchanged arguments",
        "info",
        enabled_by_default=False,
    ),
    _r(
        "low-cohesion",
        "Class whose methods split into unrelated groups (LCOM4)",
        "info",
        enabled_by_default=False,
    ),
    # ── per-file source text ──
    _r("long-file", "File exceeds the line limit", "warning", "text"),
    _r("todo-marker", "TODO/FIXME/XXX/HACK comment", "info", "text"),
    # ── infrastructure ──
    _r("syntax-error", "File failed to parse", "error", "infra"),
    _r("unreadable-file", "File could not be read", "error", "infra"),
    # ── per-package cross-file ──
    _r("import-cycle", "Circular import between modules", "error", "workspace"),
    _r(
        "contract-violation",
        "Import forbidden by an architecture contract",
        "error",
        "workspace",
    ),
    _r("high-coupling", "Module with both high fan-in and high fan-out", "warning", "workspace"),
    _r(
        "duplicate-code",
        "Duplicated token sequence (type-2 clone) across files",
        "info",
        "workspace",
    ),
    _r("orphan-interface", "Abstract class with exactly one implementation", "info", "package"),
    _r("duplicate-function-body", "Identical function bodies across files", "warning", "package"),
    _r(
        "sync-async-duplication",
        "Sync/async twin functions with near-identical bodies",
        "warning",
        "package",
    ),
)

RULE_CATALOG: dict[str, RuleMeta] = {r.id: r for r in _RULES}
RULE_IDS: frozenset[str] = frozenset(RULE_CATALOG)
DEFAULT_RULE_IDS: frozenset[str] = frozenset(r.id for r in _RULES if r.enabled_by_default)
