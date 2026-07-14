"""Concurrent review orchestration (thin wrapper around detector.py).

scan_package, SpaghettiReviewAgent, and review_packages_concurrently live
in detector.py so tests can monkeypatch PACKAGES and scan_package via
``from spaghetti import detector as ds``.
"""
from spaghetti.detector import (  # noqa: F401
    SpaghettiReviewAgent,
    review_packages_concurrently,
    scan_package,
)

__all__ = ["scan_package", "SpaghettiReviewAgent", "review_packages_concurrently"]
