"""Tests for spaghetti.baseline."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from spaghetti.baseline import (
    BaselineError,
    load_baseline,
    save_baseline,
    split_against_baseline,
)
from spaghetti.models import Issue


def _issue(fp: str, rule: str = "long-function", line: int = 1) -> Issue:
    return Issue(
        file=Path("/pkg/m.py"),
        line=line,
        severity="warning",
        rule=rule,
        message=f"{rule} {fp}",
        package="pkg",
        fingerprint=fp,
    )


def test_save_then_load_roundtrip_sorted_by_fingerprint(tmp_path: Path):
    path = tmp_path / "nested" / "baseline.json"
    save_baseline(path, [_issue("bbbb"), _issue("aaaa")], generated_by="spaghetti test")
    data = json.loads(path.read_text())
    assert data["schema_version"] == 1
    assert data["generated_by"] == "spaghetti test"
    assert list(data["findings"]) == ["aaaa", "bbbb"]
    loaded = load_baseline(path)
    assert set(loaded) == {"aaaa", "bbbb"}
    assert loaded["aaaa"].rule == "long-function"


def test_missing_file_is_an_empty_baseline(tmp_path: Path):
    assert load_baseline(tmp_path / "nope.json") == {}


@pytest.mark.parametrize(
    "content",
    ["not json", '{"schema_version": 2, "findings": {}}', '{"schema_version": 1, "findings": []}'],
)
def test_unusable_baseline_raises(tmp_path: Path, content: str):
    path = tmp_path / "b.json"
    path.write_text(content)
    with pytest.raises(BaselineError):
        load_baseline(path)


def test_split_partitions_new_known_and_fixed(tmp_path: Path):
    path = tmp_path / "b.json"
    save_baseline(path, [_issue("known"), _issue("gone")], generated_by="t")
    baseline = load_baseline(path)
    split = split_against_baseline([_issue("known"), _issue("fresh")], baseline)
    assert [i.fingerprint for i in split.new] == ["fresh"]
    assert [i.fingerprint for i in split.known] == ["known"]
    assert [e.fingerprint for e in split.fixed] == ["gone"]
