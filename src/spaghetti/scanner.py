"""Concurrent review orchestration (thin wrapper around detector.py).

scan_package and review_packages_concurrently live in detector.py so tests
can monkeypatch PACKAGES and scan_package via
``from spaghetti import detector as ds``.
"""

from spaghetti.detector import review_packages_concurrently, scan_package  # noqa: F401

__all__ = ["scan_package", "review_packages_concurrently"]
