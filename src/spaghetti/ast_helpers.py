"""AST utility functions shared across check modules."""

from __future__ import annotations

import ast
from collections.abc import Iterator
from pathlib import Path


def _nesting_depth(node: ast.AST, current: int = 0) -> int:
    """Compute maximum nesting depth from a node."""
    max_depth = current
    for child in ast.iter_child_nodes(node):
        if isinstance(child, (ast.If, ast.For, ast.While, ast.With, ast.Try, ast.ExceptHandler)):
            max_depth = max(max_depth, _nesting_depth(child, current + 1))
        else:
            max_depth = max(max_depth, _nesting_depth(child, current))
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
    traversal — the same node types increment depth — but tallies qualifying
    returns instead of tracking a maximum."""
    count = 0
    for child in ast.iter_child_nodes(node):
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            continue
        if isinstance(child, (ast.If, ast.For, ast.While, ast.With, ast.Try, ast.ExceptHandler)):
            count += _count_nested_returns(child, current + 1)
        else:
            if isinstance(child, ast.Return) and current >= 2:
                count += 1
            count += _count_nested_returns(child, current)
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
