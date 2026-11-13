"""Tests for spaghetti.diff_filter (changed-lines mode)."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from spaghetti.diff_filter import (
    DiffError,
    changed_lines,
    parse_unified_diff,
    restrict_to_changed,
)
from spaghetti.models import Issue

_DIFF = """\
diff --git a/src/m.py b/src/m.py
index 1..2 100644
--- a/src/m.py
+++ b/src/m.py
@@ -3,0 +4,2 @@ def f():
+    x = 1
+    y = 2
@@ -10 +12 @@
-old
+new
diff --git a/gone.py b/gone.py
deleted file mode 100644
--- a/gone.py
+++ /dev/null
@@ -1,3 +0,0 @@
-a
-b
-c
diff --git a/added.py b/added.py
new file mode 100644
--- /dev/null
+++ b/added.py
@@ -0,0 +1 @@
+z = 1
"""


def test_parse_unified_diff_collects_added_and_modified_lines(tmp_path: Path):
    changed = parse_unified_diff(_DIFF, tmp_path)
    assert changed[(tmp_path / "src/m.py").resolve()] == {4, 5, 12}
    assert changed[(tmp_path / "added.py").resolve()] == {1}
    assert (tmp_path / "gone.py").resolve() not in changed


def _issue(path: Path, line: int, end_line: int | None = None) -> Issue:
    return Issue(
        file=path, line=line, end_line=end_line, severity="info", rule="r", message="m", package="p"
    )


def test_restrict_to_changed_keeps_findings_whose_span_touches_a_changed_line(tmp_path: Path):
    m = (tmp_path / "m.py").resolve()
    other = (tmp_path / "other.py").resolve()
    changed = {m: {10, 11}}
    issues = [
        _issue(m, 10),  # on a changed line
        _issue(m, 5, end_line=12),  # span covers changed lines
        _issue(m, 20),  # unchanged line
        _issue(other, 10),  # file not in diff
    ]
    split = restrict_to_changed(issues, changed)
    assert [(i.line, i.file) for i in split.kept] == [(10, m), (5, m)]
    assert split.hidden == 2


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


def test_changed_lines_against_a_real_repo(tmp_path: Path):
    repo = tmp_path / "repo"
    (repo / "pkg").mkdir(parents=True)
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "t@example.com")
    _git(repo, "config", "user.name", "t")
    target = repo / "pkg" / "m.py"
    target.write_text("a = 1\nb = 2\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "init")
    target.write_text("a = 1\nb = 3\nc = 4\n")  # modify line 2, add line 3 (uncommitted)

    changed = changed_lines("HEAD", repo / "pkg")  # cwd below the toplevel still works
    assert changed == {target.resolve(): {2, 3}}


def test_changed_lines_reports_git_failures(tmp_path: Path):
    with pytest.raises(DiffError):
        changed_lines("HEAD", tmp_path)  # not a repository


def test_changed_lines_sees_non_ascii_paths(tmp_path: Path):
    repo = tmp_path / "repo"
    (repo / "pkg").mkdir(parents=True)
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "t@example.com")
    _git(repo, "config", "user.name", "t")
    target = repo / "pkg" / "café  x.py"
    target.write_text("a = 1\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "init")
    target.write_text("a = 1\nb = 2\n")
    assert changed_lines("HEAD", repo / "pkg") == {target.resolve(): {2}}
