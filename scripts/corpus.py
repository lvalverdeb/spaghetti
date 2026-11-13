"""Shared by bench.py and conformance.py: the pinned corpus in scripts/corpus.lock."""

from __future__ import annotations

import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOCK = ROOT / "scripts" / "corpus.lock"
DEFAULT_CORPUS_DIR = Path(os.environ.get("SPAGHETTI_CORPUS", ROOT / ".spaghetti" / "corpus"))


@dataclass(frozen=True)
class Repo:
    name: str
    url: str
    commit: str
    package_path: str
    set: str

    def package_root(self, corpus_dir: Path) -> Path:
        return corpus_dir / self.name / self.package_path


def load_lock(only_set: str | None = None) -> list[Repo]:
    repos: list[Repo] = []
    for line in LOCK.read_text().splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        name, url, commit, package_path, set_ = line.split()
        if only_set is None or set_ == only_set:
            repos.append(Repo(name, url, commit, package_path, set_))
    return repos


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


def ensure_checkout(repo: Repo, corpus_dir: Path) -> Path:
    """A shallow checkout of exactly the pinned commit; no-op when already there."""
    target = corpus_dir / repo.name
    head = target / ".git" / "HEAD"
    if head.exists():
        got = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=target, capture_output=True, text=True
        ).stdout.strip()
        if got == repo.commit:
            return target
    target.mkdir(parents=True, exist_ok=True)
    if not head.exists():
        _git(target, "init", "-q")
        _git(target, "remote", "add", "origin", repo.url)
    print(f"fetching {repo.name}@{repo.commit[:12]}", file=sys.stderr)
    _git(target, "fetch", "-q", "--depth", "1", "origin", repo.commit)
    _git(target, "checkout", "-q", "--detach", "FETCH_HEAD")
    return target


def count_lines(root: Path) -> int:
    total = 0
    for path in root.rglob("*.py"):
        try:
            total += path.read_text(encoding="utf-8", errors="replace").count("\n")
        except OSError:
            pass
    return total
