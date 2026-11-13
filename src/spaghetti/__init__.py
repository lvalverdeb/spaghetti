"""Spaghetti-code and architectural-smell detector for Python projects.

See :mod:`spaghetti.detector` for the scanning implementation; individual
check functions (``check_long_functions``, ``check_circular_imports``, etc.)
are accessed from there directly, e.g. ``from spaghetti import detector``.
"""

from __future__ import annotations

from spaghetti.detector import main

__all__ = ["main"]
