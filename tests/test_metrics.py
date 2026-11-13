"""Tests for spaghetti.metrics: Radon-exact cyclomatic complexity, Sonar cognitive
complexity, Halstead volume and the Maintainability Index."""

from __future__ import annotations

import ast
import math
import textwrap

import pytest

from spaghetti.metrics import (
    cognitive_complexity,
    cyclomatic_complexity,
    halstead_volume,
    maintainability_index,
    module_cyclomatic_total,
)


def _func(source: str) -> ast.FunctionDef | ast.AsyncFunctionDef:
    tree = ast.parse(textwrap.dedent(source))
    return next(n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)))


# ── cyclomatic (every case cross-checked against radon 6.0.1) ────────────────


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("def f():\n    return 1\n", 1),
        (
            "def f(x):\n    if x:\n        return 1\n    elif x > 2:\n        return 2\n    return 3\n",
            3,
        ),
        ("def f(x):\n    return 1 if x else 2\n", 2),
        ("def f(xs):\n    for x in xs:\n        pass\n    else:\n        pass\n", 3),
        ("def f(xs):\n    while xs:\n        xs.pop()\n", 2),
        (
            "def f():\n    try:\n        pass\n    except A:\n        pass\n    except B:\n        pass\n    else:\n        pass\n    finally:\n        pass\n",
            4,
        ),
        ("def f(a, b, c):\n    return a and b or c\n", 3),
        ("def f(xs):\n    return [x for x in xs if x if x > 1]\n", 4),
        ("def f(xs, ys):\n    return {x: y for x in xs for y in ys}\n", 3),
        ("def f(x):\n    assert x and x > 1\n", 2),  # radon stops at the assert
        ("def f(x):\n    with x:\n        pass\n", 1),
        ("def f(x):\n    return (lambda y: y if y else 0)(x)\n", 2),
        (
            "def f(x):\n    match x:\n        case 1:\n            pass\n        case 2:\n            pass\n        case _:\n            pass\n",
            3,
        ),
        (
            "def f(x):\n    match x:\n        case [a]:\n            pass\n        case other:\n            pass\n",
            2,
        ),
        (
            "def f(x):\n    def g():\n        if x:\n            return 1\n    class C:\n        def m(self):\n            if x:\n                pass\n    return g\n",
            1,
        ),
    ],
)
def test_cyclomatic_complexity_matches_radon(source: str, expected: int):
    assert cyclomatic_complexity(_func(source)) == expected


def test_module_cyclomatic_total_counts_methods_like_radon():
    tree = ast.parse(
        "if X:\n    pass\n"
        "def f(a):\n    return 1 if a else 2\n"
        "class C:\n    def m(self):\n        pass\n    def n(self):\n        if self:\n            pass\n"
    )
    # 1 + If(1) + f(1) + C: methods m (1+0) + n (1+1)
    assert module_cyclomatic_total(tree) == 1 + 1 + 1 + 1 + 2


# ── cognitive (examples from the SonarSource specification, appendix B/C) ────


def test_cognitive_simple_if_chain_is_flat():
    assert (
        cognitive_complexity(
            _func(
                "def f(a):\n    if a:\n        return 1\n    elif a > 2:\n        return 2\n    else:\n        return 3\n"
            )
        )
        == 3
    )


def test_cognitive_nesting_penalises_each_level():
    source = """
    def f(xs):
        for x in xs:            # +1
            if x:               # +2 (nesting 1)
                while x:        # +3 (nesting 2)
                    x -= 1
    """
    assert cognitive_complexity(_func(source)) == 6


def test_cognitive_boolean_sequences_count_once_per_operator_run():
    assert cognitive_complexity(_func("def f(a, b, c, d):\n    return a and b and c\n")) == 1
    assert cognitive_complexity(_func("def f(a, b, c, d):\n    return a and b or c and d\n")) == 3


def test_cognitive_ternary_except_and_recursion():
    source = """
    def f(n):
        try:
            x = 1 if n else 2       # +1
        except ValueError:          # +1
            return f(n - 1)         # +1 recursion
        return x
    """
    assert cognitive_complexity(_func(source)) == 3


def test_cognitive_nested_function_adds_nesting_not_increment():
    source = """
    def f(xs):
        def g(x):
            if x:                   # +2 (nested def raises nesting)
                return 1
        return [g(x) for x in xs]   # comprehension: +0
    """
    assert cognitive_complexity(_func(source)) == 2


def test_cognitive_match_and_loop_else():
    source = """
    def f(x, xs):
        match x:                    # +1
            case 1:
                if xs:              # +2
                    pass
        for y in xs:                # +1
            pass
        else:                       # +1
            pass
    """
    assert cognitive_complexity(_func(source)) == 5


# ── halstead / MI ─────────────────────────────────────────────────────────────


def test_halstead_volume_counts_operators_and_operands_like_radon():
    tree = ast.parse("def f(a, b):\n    return a + b * 2\n")
    # operators: Add, Mult (2 distinct, 2 total); operands: a, (b*2 expr), b, 2 -> 4 distinct, 4 total
    assert halstead_volume(tree) == pytest.approx(6 * math.log2(6))


def test_halstead_ignores_annotations_and_defaults():
    tree = ast.parse("def f(a: int | str = 1 + 2) -> int:\n    return a\n")
    assert halstead_volume(tree) == 0.0


def test_maintainability_index_bounds_and_ranking():
    assert maintainability_index(ast.parse(""), "") == 100.0
    trivial = "def f(a, b):\n    return a + b\n"
    assert 60 < maintainability_index(ast.parse(trivial), trivial) <= 100
    dense = "\n".join(
        f"def f{i}(a, b, c):\n    if a and b or c:\n        return a + b * c - {i}\n    return a - b / c % {i}"
        for i in range(120)
    )
    assert maintainability_index(ast.parse(dense), dense) < 10
