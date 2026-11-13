"""Shared pytest fixtures for the spaghetti test suite."""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture
def fake_package(tmp_path: Path) -> Path:
    pkg_dir = tmp_path / "fake_pkg"
    pkg_dir.mkdir()
    (pkg_dir / "clean.py").write_text("def add(a: int, b: int) -> int:\n    return a + b\n")
    (pkg_dir / "messy.py").write_text(
        "def f():\n    try:\n        pass\n    except:\n        pass\n"
    )
    return pkg_dir
