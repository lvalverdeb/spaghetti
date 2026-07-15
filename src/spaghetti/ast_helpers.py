"""AST utility functions shared across check modules."""

from __future__ import annotations

import ast
from collections.abc import Iterator


def _nesting_depth(node: ast.AST, current: int = 0) -> int:
    """Compute maximum nesting depth from a node."""
    max_depth = current
    for child in ast.iter_child_nodes(node):
        if isinstance(child, (ast.If, ast.For, ast.While, ast.With, ast.Try, ast.ExceptHandler)):
            max_depth = max(max_depth, _nesting_depth(child, current + 1))
        else:
            max_depth = max(max_depth, _nesting_depth(child, current))
    return max_depth


def _cyclomatic_complexity(node: ast.AST) -> int:
    """Approximate McCabe cyclomatic complexity."""
    complexity = 1
    for child in ast.walk(node):
        if isinstance(child, (ast.If, ast.While, ast.For, ast.AsyncFor)):
            complexity += 1
        elif isinstance(child, ast.BoolOp):
            complexity += len(child.values) - 1
        elif isinstance(child, ast.ExceptHandler):
            complexity += 1
        elif isinstance(child, ast.Assert):
            complexity += 1
        elif isinstance(child, (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)):
            complexity += 1
    return complexity


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


def _file_line_count(source: str) -> int:
    return len(source.splitlines())


def _count_own_returns(node: ast.AST) -> int:
    """Count Return statements belonging to ``node``, not to nested defs."""
    count = 0

    def visit(n: ast.AST) -> None:
        nonlocal count
        for child in ast.iter_child_nodes(n):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                continue
            if isinstance(child, ast.Return):
                count += 1
            visit(child)

    visit(node)
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
