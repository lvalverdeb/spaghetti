"""Tests for the per-file AST rule checks in spaghetti.checks.ast_per_file."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

import spaghetti.config as _cfg
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
from spaghetti.config import (
    MAX_CLASS_METHODS,
    MAX_CLASS_WMC,
    MAX_FUNC_PARAMS,
    MAX_FUNCTION_LINES,
    MAX_NESTING_DEPTH,
    MIN_METHODS_FOR_COHESION,
)


def _parse(source: str) -> ast.Module:
    return ast.parse(source)


# ── Representative check functions ──────────────────────────────────────────


def test_check_long_functions_flags_over_threshold():
    body = "\n".join(f"                x{i} = {i}" for i in range(MAX_FUNCTION_LINES + 5))
    source = f"def long_func():\n    if True:\n        if True:\n            if True:\n{body}\n    return x0\n"
    issues = check_long_functions(_parse(source), Path("f.py"), "pkg")
    assert len(issues) == 1
    assert issues[0].rule == "long-function"
    assert issues[0].severity == "warning"
    assert "long_func" in issues[0].message


def test_check_long_functions_ignores_short_function():
    source = "def short_func():\n    return 1\n"
    issues = check_long_functions(_parse(source), Path("f.py"), "pkg")
    assert issues == []


def test_check_long_functions_ignores_long_but_flat_function():
    body = "\n".join(f"    x{i} = {i}" for i in range(MAX_FUNCTION_LINES + 5))
    source = f"def long_flat_func():\n{body}\n    return x0\n"
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


def test_nesting_depth_follows_indentation():
    from spaghetti.ast_helpers import _count_nested_returns, _nesting_depth

    def depth(body: str) -> int:
        return _nesting_depth(_parse("async def f(x):\n" + body).body[0])

    # Every compound statement adds one level, including the async forms,
    # try*, match and case.
    assert depth("    if x:\n        pass\n") == 1
    assert depth("    async for a in x:\n        async with a:\n            pass\n") == 2
    assert depth("    try:\n        pass\n    except* ValueError:\n        pass\n") == 1
    assert depth("    match x:\n        case 1:\n            if x:\n                pass\n") == 3
    # Siblings of a block add nothing: elif chains, except/else/finally bodies.
    assert (
        depth("    if x:\n        pass\n    elif x:\n        pass\n    elif x:\n        pass\n")
        == 1
    )
    assert depth("    if x:\n        pass\n    else:\n        if x:\n            pass\n") == 2
    assert (
        depth("    try:\n        pass\n    except ValueError:\n        if x:\n            pass\n")
        == 2
    )
    assert depth("    try:\n        pass\n    finally:\n        if x:\n            pass\n") == 2
    # Nested returns use the same depth: an except body is at the try's level.
    returns = _count_nested_returns(
        _parse(
            "def f(x):\n    for a in x:\n        try:\n            return 1\n"
            "        except ValueError:\n            return 2\n        else:\n            return 3\n"
        ).body[0]
    )
    assert returns == 3


def test_check_bare_except_flags_bare_except():
    source = "def f():\n    try:\n        pass\n    except:\n        pass\n"
    issues = check_bare_except(_parse(source), Path("f.py"), "pkg")
    assert len(issues) == 1
    assert issues[0].rule == "bare-except"


def test_check_bare_except_allows_typed_except():
    source = "def f():\n    try:\n        pass\n    except ValueError:\n        pass\n"
    issues = check_bare_except(_parse(source), Path("f.py"), "pkg")
    assert issues == []


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


def test_check_missing_types_skips_test_functions_and_fixture_params():
    # def test_*(...) is exempt entirely — both the bare `-> None` return
    # type and fixture-injected params (`client`) pytest supplies itself.
    source = "def test_enroll_blocked_without_consent(client):\n    pass\n"
    issues = check_missing_types(_parse(source), Path("f.py"), "pkg")
    assert issues == []


def test_check_missing_types_still_flags_test_file_helper_functions():
    # A bare "helpers.py" (no tests/ parent, not conftest.py) isn't
    # recognized as a test path at all, so it stays fully checked — this is
    # the "not a test file" baseline, distinct from the tests below.
    source = "def create_consented_subject(db):\n    pass\n"
    issues = check_missing_types(_parse(source), Path("helpers.py"), "pkg")
    assert len(issues) == 2  # missing-return-type + missing-param-type


def test_check_missing_types_skips_pytest_fixture_in_conftest():
    # @pytest.fixture-decorated functions in conftest.py are still pytest
    # dependency-injection boilerplate, not application code needing types.
    source = "@pytest.fixture\ndef db_session():\n    yield session\n"
    issues = check_missing_types(_parse(source), Path("conftest.py"), "pkg")
    assert issues == []


def test_check_missing_types_skips_helper_in_tests_directory():
    # Unlike the bare-"helpers.py" baseline above, a helper under a real
    # tests/ directory is test-support code and should be exempt.
    source = "def create_consented_subject(db):\n    pass\n"
    issues = check_missing_types(_parse(source), Path("tests/helpers.py"), "pkg")
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


def test_check_excessive_params_counts_required_positional_only():
    # Seven required positionals: flagged. *args/**kwargs don't count.
    source = "def func(a, b, c, d, e, f, g, *args, **kwargs) -> None:\n    pass\n"
    issues = check_excessive_params(_parse(source), Path("f.py"), "pkg")
    assert len(issues) == 1
    assert "7 required positional" in issues[0].message


def test_check_excessive_params_ignores_options_and_self():
    # Defaulted and keyword-only parameters are options, not positional
    # confusion; self/cls never count. Public APIs with many options pass.
    source = (
        "class C:\n"
        "    def m(self, a, b, c=1, d=2, e=3, f=4, *, g=5, h=6, i=7) -> None:\n"
        "        pass\n"
    )
    assert check_excessive_params(_parse(source), Path("f.py"), "pkg") == []


def test_check_excessive_params_clean():
    source = "def func(a: int, b: str, c: float) -> None:\n    pass\n"
    issues = check_excessive_params(_parse(source), Path("f.py"), "pkg")
    assert issues == []


# ── Excessive Returns ────────────────────────────────────────────────────────


def test_check_excessive_returns_flags_many():
    source = (
        "def func(x: int) -> int:\n"
        "    if x == 1:\n"
        "        if True:\n"
        "            return 1\n"
        "    if x == 2:\n"
        "        if True:\n"
        "            return 2\n"
        "    if x == 3:\n"
        "        if True:\n"
        "            return 3\n"
        "    if x == 4:\n"
        "        if True:\n"
        "            return 4\n"
        "    return 0\n"
    )
    issues = check_excessive_returns(_parse(source), Path("f.py"), "pkg")
    assert len(issues) == 1
    assert issues[0].rule == "excessive-returns"


def test_check_excessive_returns_clean():
    source = "def func(x: int) -> int:\n    if x > 0:\n        return 1\n    return 0\n"
    issues = check_excessive_returns(_parse(source), Path("f.py"), "pkg")
    assert issues == []


def test_check_excessive_returns_ignores_flat_guard_clauses():
    source = (
        "def func(x: int) -> int:\n"
        "    if x == 1:\n"
        "        return 1\n"
        "    if x == 2:\n"
        "        return 2\n"
        "    if x == 3:\n"
        "        return 3\n"
        "    if x == 4:\n"
        "        return 4\n"
        "    return 0\n"
    )
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
    issues = check_swallowed_exceptions(_parse(source), source, Path("f.py"), "pkg")
    assert len(issues) == 1
    assert issues[0].rule == "swallowed-exception"


def test_check_swallowed_allows_commented_handler():
    # A comment on the handler documents the decision (EAFP fallback).
    source = (
        "def f():\n    try:\n        pass\n"
        "    except KeyError:\n        pass  # optional key, default applies\n"
    )
    assert check_swallowed_exceptions(_parse(source), source, Path("f.py"), "pkg") == []


def test_check_swallowed_flags_ellipsis():
    source = "def f():\n    try:\n        pass\n    except Exception:\n        ...\n"
    issues = check_swallowed_exceptions(_parse(source), source, Path("f.py"), "pkg")
    assert len(issues) == 1


def test_check_swallowed_allows_log():
    source = "def f():\n    try:\n        pass\n    except Exception as e:\n        print(e)\n"
    issues = check_swallowed_exceptions(_parse(source), source, Path("f.py"), "pkg")
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


_DEMO_LAYER_RULES = {"etl-demo": {"routes/": ["boti_data.", "boti.", "boti_dask."]}}


def test_check_layer_violations_flags_forbidden_import(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(_cfg, "LAYER_RULES", _DEMO_LAYER_RULES)
    monkeypatch.setattr(ds, "PACKAGES", {"etl-demo": Path("/fake/etl-demo/src/etl_demo")})
    pkg_dir = Path("/fake/etl-demo/src/etl_demo/routes")
    source = "from boti_data.db.sql_resource import AsyncSqlDatabaseResource\n"
    issues = check_layer_violations(_parse(source), pkg_dir / "handler.py", "etl-demo")
    assert len(issues) == 1
    assert issues[0].rule == "layer-violation"
    assert issues[0].severity == "error"


def test_check_layer_violations_clean(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(_cfg, "LAYER_RULES", _DEMO_LAYER_RULES)
    monkeypatch.setattr(ds, "PACKAGES", {"etl-demo": Path("/fake/etl-demo/src/etl_demo")})
    pkg_dir = Path("/fake/etl-demo/src/etl_demo/routes")
    source = "from etl_demo.cubes.mobile import MOBILE_CUBE\n"
    issues = check_layer_violations(_parse(source), pkg_dir / "handler.py", "etl-demo")
    assert issues == []


def test_check_layer_violations_has_no_builtin_rules():
    # No codebase-specific opinions ship by default: with LAYER_RULES empty
    # the rule is inert for every package.
    assert _cfg.LAYER_RULES == {}
    source = "from something import x\n"
    issues = check_layer_violations(_parse(source), Path("f.py"), "unknown-pkg")
    assert issues == []


# ── Transport in Library ────────────────────────────────────────────────────


def test_check_transport_flags_fastapi(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(_cfg, "LIBRARY_PACKAGES", frozenset({"etl-core"}))
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


def test_check_god_module_ignores_dto_classes():
    classes = "\n".join(f"class Dto{i}(BaseModel):\n    pass\n" for i in range(20))
    issues = check_god_module(_parse(classes), Path("f.py"), "pkg")
    assert issues == []


def test_check_god_module_still_flags_mixed_module():
    real_classes = "\n".join(f"class Real{i}:\n    def m(self) -> None: pass\n" for i in range(16))
    dtos = "\n".join(f"class Dto{i}(BaseModel):\n    pass\n" for i in range(5))
    issues = check_god_module(_parse(f"{real_classes}\n{dtos}"), Path("f.py"), "pkg")
    assert len(issues) == 1
    assert "16 classes" in issues[0].message


def test_check_god_module_ignores_protocol_and_enum_classes():
    protocols = "\n".join(
        f"class Proto{i}(Protocol):\n    def m(self) -> None: ...\n" for i in range(10)
    )
    enums = "\n".join(f'class Enum{i}(StrEnum):\n    A = "a"\n' for i in range(10))
    issues = check_god_module(_parse(f"{protocols}\n{enums}"), Path("f.py"), "pkg")
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
    source = "registry = [1, 2, 3]\n"
    issues = check_global_mutations(_parse(source), Path("f.py"), "pkg")
    assert len(issues) == 1
    assert issues[0].rule == "global-mutable"


def test_check_global_mutations_skips_upper_case_constants():
    source = "KNOWN_NAMES = {'a', 'b'}\nTABLE = {'a': 1}\n"
    assert check_global_mutations(_parse(source), Path("f.py"), "pkg") == []


def test_check_global_mutations_flags_dict():
    source = "cache = {'a': 1}\n"
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


def test_check_scope_mutations_ignores_nonlocal_closure_accumulator():
    # A closure updating its enclosing function's variable is scoped and
    # idiomatic; only module-wide ``global`` mutation is the smell.
    source = (
        "def outer() -> None:\n"
        "    state = 0\n"
        "    def bump() -> None:\n"
        "        nonlocal state\n"
        "        state += 1\n"
    )
    assert check_scope_mutations(_parse(source), Path("f.py"), "pkg") == []


def test_check_scope_mutations_skips_read_only():
    source = "counter = 0\ndef read() -> int:\n    global counter\n    return counter\n"
    issues = check_scope_mutations(_parse(source), Path("f.py"), "pkg")
    assert issues == []


def test_check_scope_mutations_skips_lazy_singleton_init():
    source = (
        "_pipeline = None\n"
        "def get_pipeline():\n"
        "    global _pipeline\n"
        "    if _pipeline is None:\n"
        "        _pipeline = load_pipeline()\n"
        "    return _pipeline\n"
    )
    issues = check_scope_mutations(_parse(source), Path("f.py"), "pkg")
    assert issues == []


def test_check_scope_mutations_still_flags_unconditional_mutation():
    # Sanity check: the lazy-init exemption must not swallow genuine hits —
    # a mutation with no `is None` guard at all is still flagged.
    source = "_pipeline = None\ndef reset_pipeline() -> None:\n    global _pipeline\n    _pipeline = load_pipeline()\n"
    issues = check_scope_mutations(_parse(source), Path("f.py"), "pkg")
    assert len(issues) == 1


def test_check_scope_mutations_still_flags_mutation_guarded_by_different_condition():
    # The guard must test *this* variable specifically, not just any `if`.
    source = (
        "_cache = None\n"
        "def refresh(force):\n"
        "    global _cache\n"
        "    if force:\n"
        "        _cache = reload()\n"
    )
    issues = check_scope_mutations(_parse(source), Path("f.py"), "pkg")
    assert len(issues) == 1


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


def test_check_message_chains_ignores_import_rooted_fluent_chain():
    # A fluent chain rooted directly in a call to an imported class (e.g.
    # testcontainers' own builder API) reflects that library's shape, not a
    # coupling problem this codebase's authors can address.
    source = (
        "from testcontainers.postgres import PostgresContainer\n\n\n"
        "def f():\n"
        "    PostgresContainer('postgres:15').with_bind_ports(5432, 5432)."
        "with_env('a', 'b').with_exposed_ports(5432).start()\n"
    )
    issues = check_message_chains(_parse(source), Path("f.py"), "pkg")
    assert issues == []


def test_check_message_chains_still_flags_project_rooted_chain():
    # An import elsewhere in the file must not exempt a chain rooted in an
    # unrelated, non-imported local/project object.
    source = "from testcontainers.postgres import PostgresContainer\n\n\ndef f():\n    a.b().c().d().e()\n"
    issues = check_message_chains(_parse(source), Path("f.py"), "pkg")
    assert len(issues) == 1
    assert issues[0].rule == "message-chain"


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


def test_check_magic_numbers_allows_http_status_codes():
    source = "def f():\n    assert response.status_code == 404\n"
    issues = check_magic_numbers(_parse(source), source, Path("f.py"), "pkg")
    assert issues == []


def test_check_magic_numbers_allows_well_known_ports():
    source = "def f():\n    connect(host, 5432)\n    connect(host, 6379)\n"
    issues = check_magic_numbers(_parse(source), source, Path("f.py"), "pkg")
    assert issues == []


def test_check_magic_numbers_still_flags_unrelated_number_outside_allowlist():
    source = "def f():\n    retry_after = 47\n"
    issues = check_magic_numbers(_parse(source), source, Path("f.py"), "pkg")
    assert len(issues) == 1
    assert "47" in issues[0].message


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


def test_check_magic_numbers_skips_dense_tuning_file():
    # A file with many distinct, non-repeating literals reads as an
    # intentional parameter table (e.g. an FMR/FNMR bake-off script), not a
    # smell — the density itself is the signal.
    values = [2, 5, 1.2, 10, 70, 0.05, 20, 34, 0.36]
    body = "\n".join(f"    v{i} = {v}" for i, v in enumerate(values))
    source = f"def bakeoff():\n{body}\n"
    issues = check_magic_numbers(_parse(source), source, Path("f.py"), "pkg")
    assert issues == []


def test_check_magic_numbers_still_flags_sparse_file():
    # A handful of magic numbers below the density threshold stays flagged.
    source = "def f():\n    a = 47\n    b = 91\n"
    issues = check_magic_numbers(_parse(source), source, Path("f.py"), "pkg")
    assert len(issues) == 2


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


def test_check_magic_strings_skips_test_files():
    source = (
        "def test_kms_round_trip():\n"
        "    assert plaintext == 'sensitive-employee-id'\n"
        "    assert decrypted == 'sensitive-employee-id'\n"
    )
    issues = check_magic_strings(_parse(source), Path("test_kms.py"), "pkg")
    assert issues == []


def test_check_magic_strings_skips_conftest():
    source = "def f():\n    if a == 'x':\n        pass\n    if b == 'x':\n        pass\n"
    issues = check_magic_strings(_parse(source), Path("conftest.py"), "pkg")
    assert issues == []


def test_check_magic_strings_skips_files_under_tests_directory():
    source = "def f():\n    if a == 'x':\n        pass\n    if b == 'x':\n        pass\n"
    issues = check_magic_strings(_parse(source), Path("tests/helpers.py"), "pkg")
    assert issues == []
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


def test_check_lazy_class_allows_protocol_class():
    # A structural-typing interface's whole point is a low method count —
    # not a sign of under-development the way it would be for a plain class.
    source = (
        "class LivenessChecker(Protocol):\n"
        "    def check(self, image_bgr: np.ndarray) -> LivenessResult: ...\n"
    )
    issues = check_lazy_class(_parse(source), Path("f.py"), "pkg")
    assert issues == []


def test_check_lazy_class_allows_str_enum_class():
    source = 'class ConsentStatus(StrEnum):\n    ACTIVE = "active"\n    REVOKED = "revoked"\n'
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


def test_check_lazy_class_allows_sqlalchemy_mapped_annotation():
    # A project's own declarative Base can be named anything, so this must
    # be recognized by column shape, not by base-class name.
    source = "class Subject(Base):\n    id: Mapped[int] = mapped_column(primary_key=True)\n"
    issues = check_lazy_class(_parse(source), Path("f.py"), "pkg")
    assert issues == []


def test_check_lazy_class_allows_sqlalchemy_mapped_qualified():
    source = "class Subject(Base):\n    id: orm.Mapped[int] = orm.mapped_column(primary_key=True)\n"
    issues = check_lazy_class(_parse(source), Path("f.py"), "pkg")
    assert issues == []


def test_check_lazy_class_allows_legacy_sqlalchemy_column():
    source = "class Subject(Base):\n    id = Column(Integer, primary_key=True)\n"
    issues = check_lazy_class(_parse(source), Path("f.py"), "pkg")
    assert issues == []


def test_check_lazy_class_exempts_any_subclass_or_decorated_class():
    # A base class or decorator supplies the purpose (marker type, config
    # subclass, Protocol, TypedDict); the smell is a bare class holding one function.
    source = "class C(SomeOtherBase):\n    x = 1\n\n@register\nclass D:\n    pass\n"
    assert check_lazy_class(_parse(source), Path("f.py"), "pkg") == []


def test_check_lazy_class_flags_bare_class_with_one_method():
    source = "class Helper:\n    def run(self):\n        return 1\n"
    issues = check_lazy_class(_parse(source), Path("f.py"), "pkg")
    assert [i.rule for i in issues] == ["lazy-class"]


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


def test_untyped_dict_ignores_dict_as_a_value_inside_annotation_metadata(tmp_path: Path):
    body = (
        "from typing import Annotated\n"
        "from pydantic import Field\n"
        "class M:\n"
        "    tags: Annotated[dict[str, int], Field(default_factory=dict)]\n"
        "    raw: Annotated[dict, Field(default_factory=dict)]\n"
    )
    issues = check_untyped_dicts(ast.parse(body), tmp_path / "m.py", "pkg")
    assert [i.line for i in issues] == [5]
