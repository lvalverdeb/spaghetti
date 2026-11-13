"""Concurrent review orchestration (thin wrapper around detector.py).

scan_package and review_packages_concurrently live in detector.py so tests
can monkeypatch PACKAGES and scan_package via
``from spaghetti import detector as ds``.
"""

from spaghetti.detector import (  # noqa: F401
    review_packages_concurrently,
    scan_package,
    scan_workspace,
)

__all__ = ["scan_package", "review_packages_concurrently", "scan_workspace"]
