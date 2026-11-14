"""Token-based clone detection (PMD CPD style) over a normalised AST stream.

The "tokens" are a pre-order linearisation of the AST rather than lexer
tokens: every node contributes its type name, identifiers become ``ID`` and
literals ``LIT``. That makes the stream identical across the Python and Rust
ports (both have the same AST) and makes the detector find type-2 clones —
copies with renamed variables, functions and attributes — not just byte-exact
ones. A window of ``min_tokens`` consecutive tokens that occurs in two or more
places, extended to its maximal length, is a clone group.
"""

from __future__ import annotations

import ast
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

__all__ = [
    "CloneGroup",
    "CloneSite",
    "DuplicatedRegion",
    "TokenStream",
    "find_clone_groups",
    "duplicated_regions",
    "token_stream",
]

TokenStream = tuple[list[str], list[int]]
"""Parallel lists: normalised token names and the 1-based line each came from."""

_SKIP = (ast.Load, ast.Store, ast.Del, ast.expr_context)
_IDENTIFIER_FIELDS = ("id", "arg", "attr", "name", "asname", "module", "rest")
_DEFS = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)

# A clone must contain some of these to be duplicated *logic*. A window made only
# of calls, assignments, imports and literals is a declaration table, an import
# block or argument forwarding: the 2026-11 audit found every false positive of
# the rule in that shape (13 of 30 samples) and no true positive without a
# control-flow, comparison or comprehension token.
_LOGIC = frozenset(
    {
        "If",
        "IfExp",
        "For",
        "AsyncFor",
        "While",
        "Try",
        "TryStar",
        "With",
        "AsyncWith",
        "Match",
        "Return",
        "Raise",
        "Assert",
        "Yield",
        "YieldFrom",
        "Await",
        "BoolOp",
        "Compare",
        "ListComp",
        "SetComp",
        "DictComp",
        "GeneratorExp",
        "Lambda",
        "AugAssign",
    }
)


def token_stream(tree: ast.Module) -> TokenStream:
    names: list[str] = []
    lines: list[int] = []

    def visit(node: ast.AST, line: int) -> None:
        if isinstance(node, _SKIP):
            return
        line = getattr(node, "lineno", line)
        if isinstance(node, _DEFS):
            # A boundary token carrying the definition's name: a window can only
            # run from one definition into the next when both sides are entering
            # a definition of the same name, so route handlers or visitor methods
            # that merely sit next to each other never fuse into one clone.
            names.append(f"\x00{node.name}")
            lines.append(line)
        if isinstance(node, ast.Constant):
            names.append("LIT")
            lines.append(line)
            return
        names.append(type(node).__name__)
        lines.append(line)
        for field in _IDENTIFIER_FIELDS:
            if isinstance(getattr(node, field, None), str):
                names.append("ID")
                lines.append(line)
        for child in ast.iter_child_nodes(node):
            visit(child, line)

    for stmt in tree.body:
        visit(stmt, stmt.lineno)
    return names, lines


@dataclass(frozen=True)
class CloneSite:
    file: Path
    start_line: int
    end_line: int

    def overlaps(self, other: CloneSite) -> bool:
        return (
            self.file == other.file
            and self.start_line <= other.end_line
            and other.start_line <= self.end_line
        )


@dataclass(frozen=True)
class CloneGroup:
    tokens: int
    sites: tuple[CloneSite, ...]


_MOD = (1 << 61) - 1
_BASE = 1_000_003
_MAX_PERIOD = 16

Position = tuple[int, int]
"""(file index, token offset)."""


def _window_hashes(encoded: list[int], width: int) -> list[int]:
    """Polynomial rolling hash of every ``width``-token window; collisions are
    resolved by comparing the real tokens before a group is accepted."""
    n = len(encoded)
    if n < width:
        return []
    power = pow(_BASE, width - 1, _MOD)
    h = 0
    for value in encoded[:width]:
        h = (h * _BASE + value) % _MOD
    hashes = [h]
    for i in range(width, n):
        h = ((h - encoded[i - width] * power) * _BASE + encoded[i]) % _MOD
        hashes.append(h)
    return hashes


def _is_periodic(window: list[int]) -> bool:
    """A window that is one short pattern repeated (``x = 1`` forty times, an
    enum or lookup table) is data layout, not duplicated logic."""
    n = len(window)
    for period in range(1, min(_MAX_PERIOD, n // 2) + 1):
        if all(window[i] == window[i - period] for i in range(period, n)):
            return True
    return False


class _Index:
    """Encoded token streams plus every window hashed, in file order."""

    def __init__(self, streams: dict[Path, TokenStream], width: int) -> None:
        self.files = list(streams)
        self.lines = [streams[f][1] for f in self.files]
        vocab: dict[str, int] = {}
        self.encoded = [
            [vocab.setdefault(name, len(vocab) + 1) for name in streams[f][0]] for f in self.files
        ]
        self.logic = frozenset(code for name, code in vocab.items() if name in _LOGIC)
        self.width = width
        self.hashes = [_window_hashes(tokens, width) for tokens in self.encoded]
        # Only a hash seen at least twice can start a group; counting first (in
        # C) keeps the position index to the few windows that recur.
        counts = Counter(h for hashes in self.hashes for h in hashes)
        self.by_hash: dict[int, list[Position]] = {}
        for file_index, hashes in enumerate(self.hashes):
            for offset, h in enumerate(hashes):
                if counts[h] > 1:
                    self.by_hash.setdefault(h, []).append((file_index, offset))

    def token_at(self, position: Position, shift: int) -> int | None:
        file_index, offset = position
        tokens = self.encoded[file_index]
        at = offset + shift
        return tokens[at] if 0 <= at < len(tokens) else None

    def window(self, position: Position) -> list[int]:
        file_index, offset = position
        return self.encoded[file_index][offset : offset + self.width]

    def same_token_everywhere(self, members: list[Position], shift: int) -> bool:
        seen = {self.token_at(m, shift) for m in members}
        return len(seen) == 1 and None not in seen

    def group_at(self, first: Position) -> CloneGroup | None:
        """The maximal clone group whose first site starts at ``first``, or
        ``None`` if the window there is not the left-maximal start of one."""
        candidates = self.by_hash.get(self.hashes[first[0]][first[1]])
        if candidates is None or candidates[0] != first:
            return None  # a group is emitted once, from its first site
        window = self.window(first)
        if _is_periodic(window):
            return None
        members = [c for c in candidates if self.window(c) == window]
        if len(members) < 2 or self.same_token_everywhere(members, -1):
            return None  # the window one step left forms a group covering this one
        length = self.width
        while self.same_token_everywhere(members, length):
            length += 1
        if self.logic.isdisjoint(self.encoded[first[0]][first[1] : first[1] + length]):
            return None  # data, imports or argument forwarding, not logic
        kept = self._drop_overlapping(members, length)
        if len(kept) < 2:
            return None
        return CloneGroup(length, tuple(self._site(m, length) for m in kept))

    @staticmethod
    def _drop_overlapping(members: list[Position], length: int) -> list[Position]:
        kept: list[Position] = []
        for member in members:
            if all(m[0] != member[0] or member[1] >= m[1] + length for m in kept):
                kept.append(member)
        return kept

    def _site(self, position: Position, length: int) -> CloneSite:
        file_index, offset = position
        lines = self.lines[file_index]
        return CloneSite(
            self.files[file_index], lines[offset], max(lines[offset : offset + length])
        )


def find_clone_groups(streams: dict[Path, TokenStream], min_tokens: int) -> list[CloneGroup]:
    """Maximal groups of ≥2 sites sharing at least ``min_tokens`` consecutive
    normalised tokens. ``streams`` is iterated in its given order, which fixes
    the order of the result and the first site of each group."""
    if min_tokens < 1:
        return []
    index = _Index(streams, min_tokens)
    groups: list[CloneGroup] = []
    for file_index, hashes in enumerate(index.hashes):
        for offset in range(len(hashes)):
            group = index.group_at((file_index, offset))
            if group is not None:
                groups.append(group)
    return groups


@dataclass(frozen=True)
class DuplicatedRegion:
    """A span of one file that is duplicated elsewhere, with every other site
    any of its clone groups pointed at."""

    file: Path
    start_line: int
    end_line: int
    others: tuple[CloneSite, ...]


def duplicated_regions(groups: list[CloneGroup], file_order: list[Path]) -> list[DuplicatedRegion]:
    """Collapse clone sites within a file that overlap by at least half of the
    smaller span into one region each, so a stretch of code covered by several
    cascading groups is reported once while two distinct clones that merely
    touch stay separate. Regions come out in ``file_order`` then by start line."""
    sites_by_file: dict[Path, list[tuple[CloneSite, tuple[CloneSite, ...]]]] = {}
    for group in groups:
        for site in group.sites:
            others = tuple(s for s in group.sites if s is not site)
            sites_by_file.setdefault(site.file, []).append((site, others))

    regions: list[DuplicatedRegion] = []
    for path in file_order:
        entries = sorted(
            sites_by_file.get(path, ()), key=lambda e: (e[0].start_line, e[0].end_line)
        )
        current: tuple[int, int, list[CloneSite]] | None = None
        for site, others in entries:
            if current is not None and _mostly_overlaps(site, current[0], current[1]):
                current = (current[0], max(current[1], site.end_line), current[2] + list(others))
            else:
                if current is not None:
                    regions.extend(_region(path, current))
                current = (site.start_line, site.end_line, list(others))
        if current is not None:
            regions.extend(_region(path, current))
    return regions


def _mostly_overlaps(site: CloneSite, start: int, end: int) -> bool:
    shared = min(site.end_line, end) - max(site.start_line, start) + 1
    shortest = min(site.end_line - site.start_line, end - start) + 1
    return shared * 2 >= shortest


def _region(path: Path, current: tuple[int, int, list[CloneSite]]) -> list[DuplicatedRegion]:
    """Zero or one region: a span whose only copies lie inside itself (two
    adjacent repeats merged into one block) has nothing to point at."""
    start, end, others = current
    span = CloneSite(path, start, end)
    unique = sorted(
        {o for o in others if not o.overlaps(span)},
        key=lambda s: (str(s.file), s.start_line, s.end_line),
    )
    return [DuplicatedRegion(path, start, end, tuple(unique))] if unique else []
