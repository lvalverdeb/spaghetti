"""Tests for spaghetti.churn against a real temporary git repository."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from spaghetti.churn import Churn, ChurnError, churn_factor, file_churn, rank_hotspots


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    (repo / "pkg").mkdir(parents=True)
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "a@example.com")
    _git(repo, "config", "user.name", "alice")
    hot = repo / "pkg" / "hot.py"
    cold = repo / "pkg" / "cold.py"
    cold.write_text("x = 1\n")
    hot.write_text("y = 1\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "init")
    for i in range(3):
        hot.write_text(f"y = {i + 2}\n")
        _git(repo, "add", "-A")
        _git(repo, "commit", "-qm", f"edit {i}")
    _git(repo, "config", "user.name", "bob")
    hot.write_text("y = 99\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "bob edit")
    return repo


def test_file_churn_counts_commits_and_authors(repo: Path):
    churn = file_churn([repo / "pkg"], months=6)
    assert churn[(repo / "pkg" / "hot.py").resolve()] == Churn(commits=5, authors=2)
    assert churn[(repo / "pkg" / "cold.py").resolve()] == Churn(commits=1, authors=1)


def test_file_churn_outside_a_repo_raises(tmp_path: Path):
    with pytest.raises(ChurnError):
        file_churn([tmp_path], months=6)


def test_churn_factor_is_relative_and_bounded():
    assert churn_factor(None, max_commits=10) == 1.0
    assert churn_factor(Churn(0, 0), max_commits=10) == 1.0
    assert churn_factor(Churn(10, 1), max_commits=10) == 2.0
    assert 1.0 < churn_factor(Churn(3, 1), max_commits=10) < 2.0


def test_rank_hotspots_prefers_tangled_and_hot_over_tangled_and_cold():
    hot, cold, clean = Path("/r/hot.py"), Path("/r/cold.py"), Path("/r/clean.py")
    penalties = {hot: 6.0, cold: 7.0, clean: 0.0}
    churn = {hot: Churn(20, 3), cold: Churn(1, 1), clean: Churn(20, 5)}
    spots = rank_hotspots(penalties, churn, display={p: p.name for p in penalties})
    assert [h.file for h in spots] == ["hot.py", "cold.py"]  # clean.py never a hotspot
    assert spots[0].score == 12.0 and spots[0].commits == 20 and spots[0].authors == 3
    assert spots[1].score < spots[1].penalty * 1.3


def test_file_churn_sees_non_ascii_paths(repo: Path):
    odd = repo / "pkg" / "café.py"
    odd.write_text("z = 1\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "non-ascii")
    churn = file_churn([repo / "pkg"], months=6)
    assert churn[odd.resolve()] == Churn(commits=1, authors=1)


def test_file_churn_sees_paths_with_unicode_line_separators(repo: Path):
    odd = repo / "pkg" / "a b.py"
    odd.write_text("z = 1\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "u2028")
    churn = file_churn([repo / "pkg"], months=6)
    assert churn[odd.resolve()] == Churn(commits=1, authors=1)
