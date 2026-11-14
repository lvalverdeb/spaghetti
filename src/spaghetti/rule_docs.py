"""Per-rule documentation: why it matters, how to fix it, what else detects it.

The catalog in :mod:`spaghetti.rules` says *what* each rule is; this module
says *why* and *how*. Both ``docs/rules/`` (via ``scripts/gen_rule_docs.py``)
and the MCP ``explain_rule`` tool render from here, and a test asserts every
catalogued rule has an entry, so the prose cannot drift from the code.

``thresholds`` names the :mod:`spaghetti.config` constants the rule reads so
the generated page shows the live default and the ``[tool.spaghetti.thresholds]``
key that tunes it.
"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = ["RuleDoc", "RULE_DOCS"]


@dataclass(frozen=True)
class RuleDoc:
    why: str
    fix: str
    equivalents: tuple[str, ...] = ()
    thresholds: tuple[str, ...] = ()


def _d(why: str, fix: str, *equivalents: str, thresholds: tuple[str, ...] = ()) -> RuleDoc:
    return RuleDoc(why, fix, equivalents, thresholds)


RULE_DOCS: dict[str, RuleDoc] = {
    # ── size and complexity ──
    "long-function": _d(
        "A function that does not fit on a screen is hard to name, test and review; "
        "its length is usually several responsibilities sharing one scope.",
        "Extract Method: name each block that has its own comment or blank-line "
        "separation and move it out, passing the few values it needs. Flat, sequential "
        "pipelines are tolerated up to a low nesting depth.",
        "Pylint R0915 too-many-statements",
        "Ruff PLR0915",
        "SonarQube python:S138",
        thresholds=("MAX_FUNCTION_LINES", "LONG_FUNCTION_FLAT_NESTING_MAX"),
    ),
    "high-complexity": _d(
        "Cyclomatic complexity counts independent paths; each one is a test case you "
        "owe and a branch a reader must hold in mind. Computed exactly as Radon does.",
        "Replace branch ladders with a dispatch table or polymorphism, hoist guard "
        "clauses to the top and return early, and split the function along its "
        "decision clusters.",
        "Ruff C901 (mccabe)",
        "Pylint R1260 too-complex",
        "SonarQube python:S1541",
        "Radon cc",
        thresholds=("COMPLEXITY_THRESHOLD",),
    ),
    "high-cognitive-complexity": _d(
        "Sonar's cognitive complexity scores what makes code hard to *read* — nesting, "
        "breaks in linear flow, mixed boolean operators — rather than paths. It is the "
        "metric most correlated with reviewer effort.",
        "Flatten nesting with early returns and extracted helpers; one condition per "
        "`if`; replace nested loops over the same data with a single comprehension or a "
        "generator pipeline.",
        "SonarQube python:S3776",
        thresholds=("COGNITIVE_COMPLEXITY_THRESHOLD",),
    ),
    "low-maintainability": _d(
        "The Maintainability Index folds Halstead volume, cyclomatic complexity, lines "
        "and comment ratio into one 0–100 number per file; below the threshold a file "
        "is dense, branchy *and* long at once.",
        "Split the module by responsibility, then apply the complexity and length fixes "
        "to what remains. Adding comments alone raises MI only marginally.",
        "Radon mi",
        "Visual Studio Maintainability Index",
        thresholds=("MIN_MAINTAINABILITY_INDEX",),
    ),
    "long-file": _d(
        "A file past a few hundred lines is a module with several reasons to change; "
        "merge conflicts and import-time cost follow.",
        "Split by cohesion: group the functions that share private helpers or state into "
        "their own module and re-export from the package `__init__` if the public path "
        "must stay.",
        "Pylint C0302 too-many-lines",
        "SonarQube python:S104",
        thresholds=("MAX_FILE_LINES",),
    ),
    "deep-nesting": _d(
        "Every nesting level is a precondition the reader must carry; past four or five "
        "the indentation itself hides which branch a line belongs to.",
        "Invert conditions into guard clauses that `return`/`continue` early, extract the "
        "inner block into a named function, or replace nested loops with a comprehension.",
        "Pylint R1702 too-many-nested-blocks",
        "SonarQube python:S134",
        thresholds=("MAX_NESTING_DEPTH",),
    ),
    "excessive-returns": _d(
        "Many return points mean many ways the function can finish; the combination with "
        "branching makes the result type and invariants hard to state.",
        "Collapse a branch ladder that returns different values into a dictionary lookup "
        "or a single result variable; keep early returns only for guard clauses.",
        "Pylint R0911 too-many-return-statements",
        "Ruff PLR0911",
        "SonarQube python:S1142",
        thresholds=("MAX_RETURNS",),
    ),
    # ── signatures ──
    "too-many-params": _d(
        "Long parameter lists are hard to call correctly and usually mean several values "
        "that travel together have no name of their own.",
        "Introduce Parameter Object: bundle the related arguments into a dataclass (or "
        "pass the object they came from); make rarely used ones keyword-only with "
        "defaults. `self`, `cls`, `*args` and `**kwargs` do not count.",
        "Pylint R0913 too-many-arguments",
        "Ruff PLR0913",
        "SonarQube python:S107",
        thresholds=("MAX_FUNC_PARAMS",),
    ),
    "boolean-flag-params": _d(
        "Several boolean flags multiply the behaviours one function implements (2ⁿ) and "
        "read as `f(data, True, False, True)` at the call site.",
        "Split into one function per behaviour, or replace the flags with an `Enum` or "
        "a small options dataclass; at minimum make them keyword-only.",
        "Ruff FBT001/FBT002 (flake8-boolean-trap)",
        thresholds=("MIN_BOOLEAN_FLAGS",),
    ),
    "missing-return-type": _d(
        "Without a return annotation callers guess, type checkers give up at the "
        "boundary, and the contract lives only in the docstring.",
        "Annotate the return type; use `-> None` for procedures. Private (underscore) "
        "functions and tests are exempt.",
        "Ruff ANN201",
        "mypy --disallow-untyped-defs",
    ),
    "missing-param-type": _d(
        "An unannotated public parameter forces every caller to read the body to learn "
        "what to pass.",
        "Annotate the parameter; prefer `Protocol`s or ABCs over concrete classes when "
        "several types are accepted.",
        "Ruff ANN001",
    ),
    "untyped-dict": _d(
        "A bare `dict` in a signature says nothing about keys or values; it is a type "
        "hole that spreads to everything downstream.",
        "Write `dict[str, int]`, or better a `TypedDict` or dataclass when the keys are fixed.",
        "mypy --disallow-any-generics",
    ),
    # ── error handling ──
    "swallowed-exception": _d(
        "An `except` that only `pass`es, logs nothing or returns a default hides failures "
        "until they surface somewhere unrelated.",
        "Catch the narrowest type, handle it (log with context, substitute a value *and* "
        "say why) or re-raise. A comment or logging call inside the block satisfies the "
        "rule.",
        "Ruff S110 (try-except-pass)",
    ),
    "bare-except": _d(
        "`except:` also catches `KeyboardInterrupt`, `SystemExit` and `MemoryError`, "
        "turning a clean shutdown into a hang.",
        "Name the exception type you expect; use `except Exception:` only at a top-level "
        "boundary that logs and re-raises or exits.",
        "Ruff E722",
        "Pylint W0702 bare-except",
    ),
    # ── duplication ──
    "duplicate-branch": _d(
        "Two branches with identical bodies mean the condition is irrelevant or one "
        "branch is a copy-paste bug waiting to diverge.",
        "Delete the condition if both branches are truly the same; otherwise fix the "
        "branch that should differ.",
        "SonarQube python:S1871",
    ),
    "duplicate-function-body": _d(
        "Two functions with byte-identical bodies across files will be fixed in one "
        "place and not the other.",
        "Keep one, import it from the other module; if the copies must diverge, make the "
        "difference a parameter.",
        "SonarQube python:S4144",
        thresholds=("DEFAULT_MIN_DUPLICATE_LINES",),
    ),
    "duplicate-code": _d(
        "Type-2 clones (same structure, renamed identifiers and literals) are the "
        "duplication tools like PMD-CPD and Sonar report; renaming variables does not "
        "make a copy independent. Only clones containing logic count: import blocks, "
        "declaration tables and argument forwarding are not reported, and a clone never "
        "runs from one function or class into a differently named neighbour.",
        "Extract the shared logic into one function parameterised by what differs; for "
        "test setup prefer fixtures or parametrisation over copied blocks.",
        "SonarQube duplicated blocks (CPD)",
        "PMD CPD",
        "jscpd",
        thresholds=("MIN_CLONE_TOKENS",),
    ),
    "sync-async-duplication": _d(
        "A sync/async twin pair whose bodies are near-identical doubles every bug fix, "
        "and the two drift apart silently.",
        "Push the shared logic into pure helpers both variants call, or generate one "
        "from the other (`unasync`), or keep only the async variant and wrap it for sync "
        "callers.",
        thresholds=("DEFAULT_TWIN_SIMILARITY", "MIN_TWIN_FUNCTION_LINES"),
    ),
    # ── classes ──
    "god-class": _d(
        "A class with too many methods, attributes or total complexity is a module in "
        "disguise; everything depends on it and nobody dares change it.",
        "Extract Class along the attribute clusters its methods actually use (the LCOM "
        "groups); delegate to the new collaborators instead of inheriting from them.",
        "Pylint R0902 too-many-instance-attributes",
        "Pylint R0904 too-many-public-methods",
        "Ruff PLR0904",
        thresholds=("MAX_CLASS_METHODS", "MAX_CLASS_ATTRS", "MAX_CLASS_WMC"),
    ),
    "lazy-class": _d(
        "A class with fewer than two methods is usually a record with ceremony, or a "
        "function that wanted to be a class.",
        "Make it a `dataclass`/`NamedTuple` if it holds data, a plain function if it "
        "holds behaviour. Exceptions, Protocols, Enums and ABCs are exempt.",
        "Pylint R0903 too-few-public-methods",
        thresholds=("MIN_CLASS_METHODS",),
    ),
    "low-cohesion": _d(
        "LCOM4 counts the connected groups of methods that share attributes; more than "
        "one group means the class is several classes sharing a name.",
        "Split along the groups the message lists; each becomes its own class, or the "
        "stateless group becomes module-level functions.",
        "CodeScene / SonarQube LCOM4 metric",
        thresholds=("MIN_METHODS_FOR_COHESION", "MIN_STATEFUL_METHOD_FRACTION"),
    ),
    "deep-inheritance": _d(
        "Behaviour inherited through four or more levels can only be understood by "
        "reading every ancestor; overrides interact in ways nobody designed.",
        "Flatten: compose the behaviours as collaborators or mixins with one job each, "
        "and keep hierarchies to a base plus concrete leaves.",
        "Pylint R0901 too-many-ancestors",
        thresholds=("MAX_INHERITANCE_DEPTH",),
    ),
    "orphan-interface": _d(
        "An abstract class with exactly one implementation is speculative generality: "
        "an indirection paid for every day for a flexibility nobody uses.",
        "Collapse the ABC into its only implementation; reintroduce the abstraction when "
        "the second implementation actually arrives.",
    ),
    "pass-through-method": _d(
        "A method whose body only forwards its unchanged arguments to another object is "
        "a Middle Man: one more place to update for no added meaning.",
        "Let callers use the delegate directly, or give the method a reason to exist "
        "(validation, translation, a narrower interface). Intended Facades are the "
        "common false positive, which is why this rule ships off.",
    ),
    "encapsulation-violation": _d(
        "Reaching into `obj._private` from another class couples the reader to an "
        "implementation detail the owner may change without notice.",
        "Add the operation to the owning class, or expose the value through a property "
        "or method that states the contract.",
        "Pylint W0212 protected-access",
        "Ruff SLF001",
    ),
    "message-chain": _d(
        "`a.b().c.d()` couples the caller to the shape of three objects it does not "
        "own; a change in any of them breaks it (Law of Demeter).",
        "Hide Delegate: ask the first object for what you need, or pass the final object "
        "in directly.",
        thresholds=("MAX_MESSAGE_CHAIN_DEPTH",),
    ),
    "excessive-decorators": _d(
        "Many stacked decorators compose behaviours in an order the reader must "
        "simulate, and hide the function's real signature.",
        "Combine them into one purpose-built decorator, or move cross-cutting concerns "
        "(caching, retry, auth) to the framework layer that owns them.",
        thresholds=("MAX_DECORATORS",),
    ),
    # ── module structure ──
    "god-module": _d(
        "A module exporting too many public names has no theme; importers take the "
        "whole thing and the import graph fills with hubs.",
        "Split into cohesive modules and give the package `__init__` an explicit "
        "`__all__` of what is genuinely public.",
        thresholds=("MAX_PUBLIC_SYMBOLS",),
    ),
    "global-mutable": _d(
        "A mutable object at module scope is shared state across every importer and "
        "every test; it makes order-dependent bugs possible.",
        "Make it immutable (tuple, frozenset, `Final` constant), or move it into an "
        "object that is created explicitly and passed where needed.",
    ),
    "scope-mutation": _d(
        "`global`/`nonlocal` writes mean a function's effect is not visible in its "
        "signature; callers cannot reason about it locally.",
        "Return the new value and let the caller assign it, or hold the state in an "
        "object the function receives.",
        "Pylint W0603 global-statement",
        "Ruff PLW0603",
    ),
    "dead-code": _d(
        "Statements after `return`/`raise`/`break`/`continue` never run; they are "
        "misleading at best and a sign of a botched edit at worst.",
        "Delete them, or move them before the exit if they were meant to run.",
        "Pylint W0101 unreachable",
        "SonarQube python:S1763",
    ),
    "mutable-default": _d(
        "A mutable default (`def f(x=[])`) is created once and shared by every call; "
        "the second caller sees the first caller's data.",
        "Default to `None` and create the object inside the function, or use "
        "`dataclasses.field(default_factory=...)`.",
        "Ruff B006",
        "Pylint W0102 dangerous-default-value",
    ),
    "magic-number": _d(
        "An unexplained literal carries a meaning the reader must reverse-engineer and "
        "is duplicated wherever the meaning is reused.",
        "Name it as a module-level constant (or an `Enum` member) where the meaning "
        "lives. Off by default: index arithmetic and obvious constants dominate real "
        "hits.",
        "Ruff PLR2004 magic-value-comparison",
        "SonarQube python:S109",
    ),
    "magic-string": _d(
        "The same string compared for equality in several places is a stringly-typed "
        "enum; a typo in one copy is a silent bug.",
        "Replace with an `Enum` or a named constant. Off by default: domain vocabulary "
        "(schema keywords, token strings) is the usual hit.",
        "SonarQube python:S1192",
    ),
    "missing-else": _d(
        "A multi-statement `if` without `else` leaves the negative path implicit; the "
        "reader cannot tell if it was considered.",
        "Add the `else` (or a comment stating the fall-through is intended). Off by "
        "default: the audit found no true positives.",
        "SonarQube python:S126",
    ),
    "todo-marker": _d(
        "TODO/FIXME/XXX/HACK markers are debt with no owner or date; they accumulate "
        "until nobody trusts them.",
        "Turn each into a tracked issue and reference it, or do it now. Keep the marker "
        "only with the ticket id.",
        "Ruff FIX001–FIX004 / TD",
        "Pylint W0511 fixme",
        "SonarQube python:S1135",
    ),
    # ── imports and architecture ──
    "unused-import": _d(
        "An import nobody uses costs import time and misleads about dependencies.",
        "Remove it. Off by default: Ruff F401 owns this and does it better.",
        "Ruff F401",
        "Pylint W0611",
    ),
    "star-import": _d(
        "`from x import *` makes the namespace depend on another module's contents and "
        "defeats static analysis.",
        "Import the names you use. Off by default: Ruff F403 owns this.",
        "Ruff F403",
        "Pylint W0401 wildcard-import",
    ),
    "potential-circular-import": _d(
        "A child module importing its parent package often creates a cycle through the "
        "package `__init__`.",
        "Import the sibling module directly. Off by default: `import-cycle` detects the "
        "real cycles on the workspace graph.",
        "Pylint R0401 cyclic-import (partial)",
    ),
    "import-cycle": _d(
        "Modules that import each other can only be loaded in one order, break on "
        "`from x import name`, and cannot be tested or reused apart.",
        "Move the shared types to a leaf module both import; invert the dependency with "
        "a `Protocol`; or make one import lazy inside the function that needs it.",
        "Pylint R0401 cyclic-import",
        "import-linter",
    ),
    "high-coupling": _d(
        "A module with both high fan-in and high fan-out is a hub: every change in it "
        "ripples out, and every change around it ripples in.",
        "Split the module so that the widely imported part (stable types, constants) is "
        "separate from the part that imports many others (orchestration).",
        "CodeScene / SonarQube afferent–efferent coupling",
        thresholds=("MAX_MODULE_FAN_IN", "MAX_MODULE_FAN_OUT"),
    ),
    "layer-violation": _d(
        "An import across a configured layer boundary (`[tool.spaghetti.layers]`) means "
        "the architecture on the whiteboard and the one in the code differ.",
        "Depend on an abstraction the lower layer implements and inject it, or move the "
        "code to the layer it belongs to.",
        "import-linter forbidden contract",
        "Tach",
        thresholds=("MAX_CROSS_LAYER_IMPORTS",),
    ),
    "contract-violation": _d(
        "An import that breaks an architecture contract (`[[tool.spaghetti.contracts]]`, "
        "forbidden edges or ordered layers) is the custom rule teams actually want: the "
        "design decision, enforced.",
        "Apply the Dependency Inversion Principle: depend on a `Protocol` the lower "
        "layer implements and inject it; or add the edge to `allow` if it is a deliberate "
        "exception.",
        "import-linter contracts",
        "Tach",
        "ArchUnit (Java)",
    ),
    "transport-in-library": _d(
        "A library package importing a web framework can no longer be used without "
        "that framework, and its tests need an HTTP stack.",
        "Keep transport (FastAPI, Flask, httpx…) in an adapter package; the library "
        "exposes plain functions and dataclasses the adapter calls.",
        "import-linter forbidden contract",
    ),
    # ── infrastructure ──
    "syntax-error": _d(
        "A file that does not parse cannot be analysed, imported or run.",
        "Fix the syntax; if the file targets a newer Python than the scanner runs on, "
        "run spaghetti on that interpreter.",
        "Pylint E0001 syntax-error",
    ),
    "scan-limit": _d(
        "A file over the size limit or nested past the interpreter's recursion limit is "
        "almost always generated code or data; analysing it would cost minutes and say "
        "nothing about the hand-written code around it.",
        "Exclude generated files with `exclude`, or raise `max-file-bytes` if the file "
        "is genuinely hand-written. Nothing in it was checked.",
        thresholds=("MAX_FILE_BYTES",),
    ),
    "unreadable-file": _d(
        "A file that cannot be read (permissions, invalid encoding) is a blind spot in "
        "every report.",
        "Fix permissions or re-encode as UTF-8; exclude generated or binary paths with `exclude`.",
    ),
}
