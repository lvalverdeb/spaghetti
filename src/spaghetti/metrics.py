"""Published code metrics: McCabe cyclomatic complexity (Radon-exact), Sonar
cognitive complexity, Halstead volume and the SEI/Radon Maintainability Index.

Every function here is pure and port-independent: the Rust binary must
reproduce the same numbers, so each definition states the exact counting rule
rather than deferring to a library.
"""

from __future__ import annotations

import ast
import math
import tokenize
from collections.abc import Callable, Iterator
from io import StringIO
from typing import Any

__all__ = [
    "cyclomatic_complexity",
    "cognitive_complexity",
    "halstead_volume",
    "maintainability_index",
    "module_cyclomatic_total",
]

_SCOPE_NODES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)


# ── Cyclomatic complexity (McCabe, as counted by ``radon cc``) ────────────────


def _iter_own_scope(node: ast.AST) -> Iterator[ast.AST]:
    """Pre-order walk of ``node``'s children that does not descend into nested
    function or class definitions (lambdas are expressions and are descended),
    nor into ``assert`` statements (Radon counts the assert and stops there)."""
    for child in ast.iter_child_nodes(node):
        yield child
        if not isinstance(child, _SCOPE_NODES + (ast.Assert,)):
            yield from _iter_own_scope(child)


def _is_wildcard_case(case: ast.match_case) -> bool:
    """``case _:`` or ``case name:`` — a catch-all arm, which Radon does not count."""
    return isinstance(case.pattern, ast.MatchAs) and case.pattern.pattern is None


def _decision_points(node: ast.AST) -> int:
    """Radon's rules: ``if``/``elif``/ternary +1; ``for``/``while`` +1 plus 1 for
    an ``else``; each ``except`` +1 plus 1 for ``try/else``; ``and``/``or`` chains
    +(operands - 1); each comprehension ``for`` clause +1 plus its ``if``s;
    ``assert`` +1; ``match`` + arms, minus one when a catch-all arm exists.
    ``with`` and ``lambda`` do not count; nested defs are separate units."""
    if isinstance(node, (ast.If, ast.IfExp, ast.Assert)):
        return 1
    if isinstance(node, (ast.For, ast.AsyncFor, ast.While)):
        return 1 + bool(node.orelse)
    if isinstance(node, (ast.Try, getattr(ast, "TryStar", ast.Try))):
        return len(node.handlers) + bool(node.orelse)
    if isinstance(node, ast.BoolOp):
        return len(node.values) - 1
    if isinstance(node, ast.comprehension):
        return 1 + len(node.ifs)
    if isinstance(node, ast.Match):
        return max(0, len(node.cases) - any(_is_wildcard_case(c) for c in node.cases))
    return 0


def cyclomatic_complexity(func: ast.FunctionDef | ast.AsyncFunctionDef) -> int:
    """McCabe complexity of one function body, identical to ``radon cc``.
    Nested functions and classes are their own units and do not contribute."""
    return 1 + sum(_decision_points(child) for stmt in func.body for child in _walk_stmt(stmt))


def _walk_stmt(stmt: ast.stmt) -> Iterator[ast.AST]:
    yield stmt
    if not isinstance(stmt, _SCOPE_NODES + (ast.Assert,)):
        yield from _iter_own_scope(stmt)


def _own_decisions(stmts: list[ast.stmt]) -> int:
    return sum(_decision_points(child) for stmt in stmts for child in _walk_stmt(stmt))


def _scope_defs(stmts: list[ast.stmt]) -> Iterator[ast.AST]:
    """Function and class definitions reachable from ``stmts`` without entering
    another definition (so a class under ``if TYPE_CHECKING:`` is found)."""
    for stmt in stmts:
        for node in _walk_stmt(stmt):
            if isinstance(node, _SCOPE_NODES):
                yield node


def module_cyclomatic_total(tree: ast.Module) -> int:
    """Radon's module ``total_complexity`` (the MI input), reproduced exactly:
    1 + module-level decision points + each module-level function's own
    decision points + for each module-level class its body-level decision
    points plus, per method, 1 + the method's own decision points. Closures,
    nested classes and their contents are not counted, as in Radon."""
    total = 1 + _own_decisions(tree.body)
    for node in _scope_defs(tree.body):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            total += _own_decisions(node.body)
        else:
            total += _own_decisions(node.body)
            for member in _scope_defs(node.body):
                if isinstance(member, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    total += 1 + _own_decisions(member.body)
    return total


# ── Cognitive complexity (SonarSource, 2018 specification) ────────────────────


class _Cognitive:
    """Walks one function. Structural increments (+1 + nesting) for ``if``,
    ternary, ``match``, ``for``, ``while`` and ``except``; hybrid increments (+1,
    no nesting) for ``elif`` and ``else``; +1 per boolean-operator sequence;
    +1 per direct recursive call. Nested functions and lambdas raise the
    nesting level without incrementing. Shorthand structures (``with``,
    ``assert``, comprehensions, ``return``, ``break``) add nothing."""

    def __init__(self, name: str) -> None:
        self.name = name
        self.total = 0

    def run(self, func: ast.FunctionDef | ast.AsyncFunctionDef) -> int:
        self._body(func.body, 0)
        return self.total

    # ── statements ──

    def _body(self, stmts: list[ast.stmt], nesting: int) -> None:
        for stmt in stmts:
            self._stmt(stmt, nesting)

    def _stmt(self, node: ast.stmt, nesting: int) -> None:
        handler = _STMT_HANDLERS.get(type(node), _Cognitive._generic)
        handler(self, node, nesting)

    def _if(self, node: ast.If, nesting: int) -> None:
        self.total += 1 + nesting
        self._expr(node.test, nesting)
        self._body(node.body, nesting + 1)
        self._else_chain(node.orelse, nesting)

    def _else_chain(self, orelse: list[ast.stmt], nesting: int) -> None:
        if not orelse:
            return
        self.total += 1  # elif / else: hybrid increment, no nesting penalty
        if len(orelse) == 1 and isinstance(orelse[0], ast.If):
            elif_node = orelse[0]
            self._expr(elif_node.test, nesting)
            self._body(elif_node.body, nesting + 1)
            self._else_chain(elif_node.orelse, nesting)
        else:
            self._body(orelse, nesting + 1)

    def _loop(self, node: ast.For | ast.AsyncFor | ast.While, nesting: int) -> None:
        self.total += 1 + nesting
        self._expr(node.iter if isinstance(node, (ast.For, ast.AsyncFor)) else node.test, nesting)
        self._body(node.body, nesting + 1)
        if node.orelse:
            self.total += 1
            self._body(node.orelse, nesting + 1)

    def _try(self, node: ast.Try, nesting: int) -> None:
        self._body(node.body, nesting)
        for handler in node.handlers:
            self.total += 1 + nesting
            self._body(handler.body, nesting + 1)
        self._body(node.orelse, nesting)
        self._body(node.finalbody, nesting)

    def _match(self, node: ast.Match, nesting: int) -> None:
        self.total += 1 + nesting
        self._expr(node.subject, nesting)
        for case in node.cases:
            if case.guard is not None:
                self._expr(case.guard, nesting + 1)
            self._body(case.body, nesting + 1)

    def _nested_def(self, node: ast.FunctionDef | ast.AsyncFunctionDef, nesting: int) -> None:
        self._body(node.body, nesting + 1)

    def _nested_class(self, node: ast.ClassDef, nesting: int) -> None:
        return  # a class body is its own unit

    def _generic(self, node: ast.AST, nesting: int) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.expr):
                self._expr(child, nesting)
            elif isinstance(child, ast.stmt):
                self._stmt(child, nesting)
            else:
                self._generic(child, nesting)  # with-items, keywords, handlers ...

    # ── expressions ──

    def _expr(self, node: ast.expr, nesting: int) -> None:
        handler = _EXPR_HANDLERS.get(type(node), _Cognitive._generic_expr)
        handler(self, node, nesting)

    def _bool_op(self, node: ast.BoolOp, nesting: int) -> None:
        self.total += 1
        for value in node.values:
            self._expr(value, nesting)

    def _if_exp(self, node: ast.IfExp, nesting: int) -> None:
        self.total += 1 + nesting
        self._expr(node.test, nesting)
        self._expr(node.body, nesting + 1)
        self._expr(node.orelse, nesting + 1)

    def _lambda(self, node: ast.Lambda, nesting: int) -> None:
        self._expr(node.body, nesting + 1)

    def _call(self, node: ast.Call, nesting: int) -> None:
        if self._is_recursive_call(node):
            self.total += 1
        self._generic_expr(node, nesting)

    def _generic_expr(self, node: ast.AST, nesting: int) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.expr):
                self._expr(child, nesting)
            else:
                self._generic_expr(child, nesting)  # comprehension clauses, keywords

    def _is_recursive_call(self, call: ast.Call) -> bool:
        func = call.func
        if isinstance(func, ast.Name):
            return func.id == self.name
        return (
            isinstance(func, ast.Attribute)
            and func.attr == self.name
            and isinstance(func.value, ast.Name)
            and func.value.id in ("self", "cls")
        )


_STMT_HANDLERS: dict[type, Callable[[_Cognitive, Any, int], None]] = {
    ast.If: _Cognitive._if,
    ast.For: _Cognitive._loop,
    ast.AsyncFor: _Cognitive._loop,
    ast.While: _Cognitive._loop,
    ast.Try: _Cognitive._try,
    ast.Match: _Cognitive._match,
    ast.FunctionDef: _Cognitive._nested_def,
    ast.AsyncFunctionDef: _Cognitive._nested_def,
    ast.ClassDef: _Cognitive._nested_class,
}
if hasattr(ast, "TryStar"):
    _STMT_HANDLERS[ast.TryStar] = _Cognitive._try

_EXPR_HANDLERS: dict[type, Callable[[_Cognitive, Any, int], None]] = {
    ast.BoolOp: _Cognitive._bool_op,
    ast.IfExp: _Cognitive._if_exp,
    ast.Lambda: _Cognitive._lambda,
    ast.Call: _Cognitive._call,
}


def cognitive_complexity(func: ast.FunctionDef | ast.AsyncFunctionDef) -> int:
    """Sonar cognitive complexity of one function, nested functions included."""
    return _Cognitive(func.name).run(func)


# ── Halstead volume (Radon's operator/operand rules) ─────────────────────────


def _operand_key(node: ast.expr) -> object:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    if isinstance(node, ast.Constant):
        return node.value
    return id(node)  # any other expression is a distinct operand per occurrence


def halstead_volume(tree: ast.Module) -> float:
    """``N * log2(h)`` over the whole module, with operators = binary, unary,
    boolean, augmented-assignment and comparison operators and operands = their
    direct operands (Radon's ``HalsteadVisitor``, reproduced exactly). Operands
    are distinct per enclosing function name, as Radon keys them."""
    operators = 0
    operands = 0
    distinct_operators: set[str] = set()
    distinct_operands: set[tuple[str | None, object]] = set()

    def visit(node: ast.AST, context: str | None) -> None:
        nonlocal operators, operands
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            # Radon visits a function's body only: annotations, defaults and
            # decorators contribute nothing.
            for stmt in node.body:
                visit(stmt, node.name)
            return
        ops: list[str] = []
        opnds: list[ast.expr] = []
        if isinstance(node, ast.BinOp):
            ops, opnds = [type(node.op).__name__], [node.left, node.right]
        elif isinstance(node, ast.UnaryOp):
            ops, opnds = [type(node.op).__name__], [node.operand]
        elif isinstance(node, ast.BoolOp):
            ops, opnds = [type(node.op).__name__], list(node.values)
        elif isinstance(node, ast.AugAssign):
            ops, opnds = [type(node.op).__name__], [node.target, node.value]
        elif isinstance(node, ast.Compare):
            ops = [type(op).__name__ for op in node.ops]
            opnds = [*node.comparators, node.left]
        operators += len(ops)
        operands += len(opnds)
        distinct_operators.update(ops)
        for operand in opnds:
            distinct_operands.add((context, _operand_key(operand)))
        for child in ast.iter_child_nodes(node):
            visit(child, context)

    visit(tree, None)
    vocabulary = len(distinct_operators) + len(distinct_operands)
    return (operators + operands) * math.log2(vocabulary) if vocabulary else 0.0


# ── Maintainability Index (SEI formula, Radon's normalisation) ───────────────


def _logical_lines(tree: ast.Module) -> int:
    """Logical lines the way ``radon raw`` counts them: one per statement, plus
    one for each header line that is not itself a statement — decorators,
    ``else:``, ``except ...:``, ``finally:`` and ``case ...:``."""
    count = 0
    for node in ast.walk(tree):
        if isinstance(node, ast.stmt):
            count += 1
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            count += len(node.decorator_list)
        elif isinstance(node, ast.If):
            if node.orelse and not (len(node.orelse) == 1 and isinstance(node.orelse[0], ast.If)):
                count += 1
        elif isinstance(node, (ast.For, ast.AsyncFor, ast.While)):
            count += bool(node.orelse)
        elif isinstance(node, (ast.Try, getattr(ast, "TryStar", ast.Try))):
            count += len(node.handlers) + bool(node.orelse) + bool(node.finalbody)
        elif isinstance(node, ast.Match):
            count += len(node.cases)
    return count


def _comment_lines(source: str) -> set[int]:
    lines: set[int] = set()
    try:
        for tok in tokenize.generate_tokens(StringIO(source).readline):
            if tok.type == tokenize.COMMENT:
                lines.add(tok.start[0])
    except (tokenize.TokenError, SyntaxError, IndentationError):
        pass
    return lines


def _string_statements(tree: ast.Module) -> Iterator[ast.Expr]:
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Expr)
            and isinstance(node.value, ast.Constant)
            and isinstance(node.value.value, str)
            and node.end_lineno is not None
        ):
            yield node


def _line_stats(source: str, tree: ast.Module) -> tuple[int, int, float]:
    """(lloc, sloc, comment_percent) with Radon's conventions: sloc = physical
    lines that are neither blank, comment-only nor part of a standalone string
    statement; comment_percent = (lines carrying a ``#`` comment + non-blank
    lines of multi-line standalone strings) / sloc * 100 (``multi=True``)."""
    lines = source.splitlines()
    string_stmt_lines: set[int] = set()
    multi_lines: set[int] = set()
    for node in _string_statements(tree):
        span = range(node.lineno, node.end_lineno + 1)  # type: ignore[arg-type]
        string_stmt_lines.update(span)
        if node.end_lineno > node.lineno:  # type: ignore[operator]
            multi_lines.update(n for n in span if lines[n - 1].strip())
    sloc = sum(
        1
        for number, text in enumerate(lines, start=1)
        if text.strip() and number not in string_stmt_lines and not text.strip().startswith("#")
    )
    comments = len(_comment_lines(source)) + len(multi_lines)
    percent = comments / sloc * 100 if sloc else 0.0
    return _logical_lines(tree), sloc, percent


def maintainability_index(tree: ast.Module, source: str) -> float:
    """0-100, higher is more maintainable. ``radon mi`` ranks: A ≥ 20, B 10-19,
    C < 10. Returns 100 for an empty module."""
    volume = halstead_volume(tree)
    complexity = module_cyclomatic_total(tree)
    lloc, _sloc, comment_percent = _line_stats(source, tree)
    if volume <= 0 or lloc <= 0:
        return 100.0  # Radon's convention for a module with no operators
    raw = (
        171
        - 5.2 * math.log(volume)
        - 0.23 * complexity
        - 16.2 * math.log(lloc)
        + 50 * math.sin(math.sqrt(2.46 * math.radians(comment_percent)))
    )
    return min(max(0.0, raw * 100 / 171), 100.0)
