"""AST utility functions shared across check modules."""

from __future__ import annotations

import ast
from collections.abc import Iterator
from pathlib import Path

# Compound statements whose body is indented one level deeper than they are.
# ``match`` and each ``case`` both count: a case body sits two levels inside
# the enclosing block. ``except``/``else``/``finally`` bodies are siblings of
# the ``try`` body, and an ``elif`` is a sibling of its ``if``, so neither adds
# a level — see ``_child_depth``.
_NESTING_NODES = (
    ast.If,
    ast.For,
    ast.AsyncFor,
    ast.While,
    ast.With,
    ast.AsyncWith,
    ast.Try,
    getattr(ast, "TryStar", ast.Try),
    ast.Match,
    ast.match_case,
)


def _child_depth(parent: ast.AST, child: ast.AST, current: int) -> int:
    """Nesting depth of ``child``'s own body given that ``parent``'s body is
    at ``current``: one deeper for a compound statement, the same for an
    ``elif`` or anything else. An ``elif`` is the sole ``orelse`` of its
    ``if`` *and* starts in the same column; ``else:`` followed by an indented
    ``if`` has the same tree shape but sits one column level deeper, which is
    exactly the level the reader sees."""
    if isinstance(child, _NESTING_NODES) and not (
        isinstance(parent, ast.If)
        and isinstance(child, ast.If)
        and len(parent.orelse) == 1
        and parent.orelse[0] is child
        and child.col_offset == parent.col_offset
    ):
        return current + 1
    return current


def _nesting_depth(node: ast.AST, current: int = 0) -> int:
    """Maximum block nesting depth inside ``node``: the number of indentation
    levels a reader must carry, counting every compound statement once."""
    max_depth = current
    for child in ast.iter_child_nodes(node):
        max_depth = max(max_depth, _nesting_depth(child, _child_depth(node, child, current)))
    return max_depth


def _loc(node: ast.AST) -> dict[str, int | None]:
    """``Issue`` location kwargs for ``node``: 1-based start column and the end
    position. Columns are raw ``ast`` byte offsets at this point;
    ``detector.scan_package`` rewrites them to character columns once the
    source lines are at hand."""
    end_col = getattr(node, "end_col_offset", None)
    return {
        "column": node.col_offset + 1,
        "end_line": getattr(node, "end_lineno", None),
        "end_column": end_col + 1 if end_col is not None else None,
    }


def _line_count(node: ast.AST) -> int:
    """End line - start line + 1."""
    if hasattr(node, "end_lineno") and node.end_lineno is not None:
        return node.end_lineno - node.lineno + 1
    return 0


def _has_return_type_hint(node: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    return node.returns is not None


def _param_has_type_hint(arg: ast.arg) -> bool:
    return arg.annotation is not None


def _is_private(name: str) -> bool:
    return name.startswith("_")


def _is_test_function(name: str) -> bool:
    """True for a pytest test function's own name (``test_*``), not a test
    *file*'s helper functions — ``create_consented_subject`` in a test file
    should still be checked, only the bare ``test_*`` functions and their
    fixture-injected parameters are pytest-convention boilerplate."""
    return name.startswith("test_")


def _is_test_file(filepath: Path) -> bool:
    """True for a pytest test module: ``test_*.py``/``*_test.py``,
    ``conftest.py``, or anything under a directory literally named
    ``tests``."""
    name = filepath.name
    if name == "conftest.py" or name.startswith("test_") or name.endswith("_test.py"):
        return True
    return "tests" in filepath.parts[:-1]


def _file_line_count(source: str) -> int:
    return len(source.splitlines())


def _count_nested_returns(node: ast.AST, current: int = 0) -> int:
    """Count Return statements at nesting depth >= 2 within ``node``, not
    counting returns in nested defs/lambdas. Mirrors ``_nesting_depth``'s
    traversal (``_child_depth``) but tallies qualifying returns instead of
    tracking a maximum."""
    count = 0
    for child in ast.iter_child_nodes(node):
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            continue
        if isinstance(child, ast.Return) and current >= 2:
            count += 1
        count += _count_nested_returns(child, _child_depth(node, child, current))
    return count


def _dump_stmts(stmts: list[ast.stmt]) -> str:
    return "\n".join(ast.dump(s, annotate_fields=False) for s in stmts)


def _is_trivial_body(node: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    """True for stub-like bodies (pass / docstring-only / ellipsis) that shouldn't
    count as meaningful duplication if repeated."""
    body = node.body
    meaningful = [
        s
        for s in body
        if not (
            isinstance(s, ast.Expr)
            and isinstance(s.value, ast.Constant)
            and isinstance(s.value.value, str)
        )
    ]
    if not meaningful:
        return True
    if len(meaningful) == 1 and isinstance(meaningful[0], ast.Pass):
        return True
    if (
        len(meaningful) == 1
        and isinstance(meaningful[0], ast.Expr)
        and isinstance(meaningful[0].value, ast.Constant)
        and meaningful[0].value.value is Ellipsis
    ):
        return True
    if len(meaningful) == 1 and isinstance(meaningful[0], ast.Raise):
        return True
    return False


def _walk_with_class_context(tree: ast.AST) -> Iterator[tuple[ast.AST, str | None]]:
    """Yield (node, enclosing_class_name_or_None) for every node in the tree."""

    def visit(node: ast.AST, class_name: str | None) -> Iterator[tuple[ast.AST, str | None]]:
        for child in ast.iter_child_nodes(node):
            new_class_name = class_name
            if isinstance(child, ast.ClassDef):
                new_class_name = child.name
            yield child, class_name
            yield from visit(child, new_class_name)

    yield from visit(tree, None)


def walk(node: ast.AST) -> list[ast.AST]:
    """``list(ast.walk(node))``, memoized on the node.

    Thirty-odd per-file checks each walk the whole module; on the benchmark
    corpus that walk is more than half the scan time. The list is stored on
    the node object itself (``ast`` nodes accept arbitrary attributes and
    neither ``_fields`` traversal nor ``unparse`` sees it), so it is freed
    with the tree and cannot go stale unless the tree is mutated after the
    first walk — which no check does.
    """
    cached = getattr(node, "_spaghetti_walk", None)
    if cached is None:
        cached = list(ast.walk(node))
        node._spaghetti_walk = cached  # type: ignore[attr-defined]
    return cached
