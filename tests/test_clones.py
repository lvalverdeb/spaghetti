"""Tests for spaghetti.clones (token-based type-2 clone detection)."""

from __future__ import annotations

import ast
from pathlib import Path

from spaghetti.clones import (
    CloneGroup,
    CloneSite,
    duplicated_regions,
    find_clone_groups,
    token_stream,
)

_BODY = """
def {name}(items, limit):
    total = 0
    seen = set()
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        if item.value > limit:
            total += item.value * 2
        else:
            total -= 1
    return total, len(seen)
"""


def _stream(source: str):
    return token_stream(ast.parse(source))


def test_token_stream_normalises_identifiers_and_literals():
    names, lines = _stream("x = 1\ny = 'a'\n")
    assert names == ["Assign", "Name", "ID", "LIT", "Assign", "Name", "ID", "LIT"]
    assert lines == [1, 1, 1, 1, 2, 2, 2, 2]


def test_renamed_copy_is_detected_as_one_group(tmp_path: Path):
    a = tmp_path / "a.py"
    b = tmp_path / "b.py"
    original = _BODY.format(name="summarize")
    renamed = (
        _BODY.format(name="tally")
        .replace("items", "rows")
        .replace("total", "acc")
        .replace("seen", "done")
        .replace("limit", "cap")
        .replace("2", "7")
    )
    groups = find_clone_groups({a: _stream(original), b: _stream(renamed)}, min_tokens=30)
    assert len(groups) == 1
    (group,) = groups
    assert [s.file for s in group.sites] == [a, b]
    assert group.sites[0].start_line == 2 and group.sites[0].end_line == 13
    assert group.tokens == len(_stream(original)[0])


def test_no_group_below_min_tokens_or_for_distinct_code(tmp_path: Path):
    a = tmp_path / "a.py"
    b = tmp_path / "b.py"
    assert find_clone_groups({a: _stream("x = 1\n"), b: _stream("y = 2\n")}, min_tokens=5) == []
    different = "def g(a):\n    return [a for _ in range(3)]\n"
    assert (
        find_clone_groups(
            {a: _stream(_BODY.format(name="f")), b: _stream(different)}, min_tokens=10
        )
        == []
    )


def test_three_copies_form_one_group_and_overlaps_are_dropped(tmp_path: Path):
    files = {}
    for i in range(3):
        files[tmp_path / f"m{i}.py"] = _stream(_BODY.format(name=f"f{i}"))
    groups = find_clone_groups(files, min_tokens=20)
    assert len(groups) == 1
    assert len(groups[0].sites) == 3
    # repeated trivial statements in one file never pair with themselves
    rep = tmp_path / "rep.py"
    assert find_clone_groups({rep: _stream("x = 1\n" * 40)}, min_tokens=8) == []


def test_duplicated_regions_merge_cascading_sites_per_file():
    a, b, c = Path("a.py"), Path("b.py"), Path("c.py")
    groups = [
        CloneGroup(60, (CloneSite(a, 10, 20), CloneSite(b, 1, 11), CloneSite(c, 5, 15))),
        CloneGroup(55, (CloneSite(a, 14, 26), CloneSite(b, 5, 17))),
        CloneGroup(50, (CloneSite(a, 40, 48), CloneSite(c, 30, 38))),
        # touches the first region by one line only: a distinct clone, kept apart
        CloneGroup(50, (CloneSite(a, 26, 39), CloneSite(c, 60, 73))),
    ]
    regions = duplicated_regions(groups, [a, b, c])
    assert [(r.file, r.start_line, r.end_line) for r in regions] == [
        (a, 10, 26),
        (a, 26, 39),
        (a, 40, 48),
        (b, 1, 17),
        (c, 5, 15),
        (c, 30, 38),
        (c, 60, 73),
    ]
    assert regions[0].others == (CloneSite(b, 1, 11), CloneSite(b, 5, 17), CloneSite(c, 5, 15))


def test_duplicated_regions_drop_a_span_whose_only_copy_is_inside_itself():
    a = Path("a.py")
    groups = [CloneGroup(50, (CloneSite(a, 10, 20), CloneSite(a, 16, 26)))]
    assert duplicated_regions(groups, [a]) == []
