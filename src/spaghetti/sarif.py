"""SARIF 2.1.0 output (``--format sarif``).

One run, one tool (``spaghetti``), one result per finding. Findings silenced by
an inline ``# spaghetti-ignore`` marker are emitted as suppressed results so
reviewers can still see them; findings matched by a baseline carry
``baselineState: "unchanged"`` and the rest ``"new"`` when a baseline is in
use. File URIs are relative to the working directory under ``%SRCROOT%``.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from spaghetti.models import Issue
from spaghetti.rules import RULE_CATALOG

SARIF_VERSION = "2.1.0"
SARIF_SCHEMA = (
    "https://docs.oasis-open.org/sarif/sarif/v2.1.0/errata01/os/schemas/sarif-schema-2.1.0.json"
)
INFORMATION_URI = "https://github.com/lvalverdeb/spaghetti"
FINGERPRINT_KEY = "spaghetti/v1"

_LEVEL = {"error": "error", "warning": "warning", "info": "note"}


def _rules_component() -> list[dict[str, Any]]:
    return [
        {
            "id": meta.id,
            "shortDescription": {"text": meta.summary},
            "defaultConfiguration": {"level": _LEVEL[meta.default_severity]},
            "properties": {"tier": meta.tier},
        }
        for meta in RULE_CATALOG.values()
        if meta.enabled_by_default
    ]


def _relative_uri(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root).as_posix()
    except ValueError:
        return Path(os.path.relpath(path, root)).as_posix()


def _region(issue: Issue) -> dict[str, int]:
    region: dict[str, int] = {"startLine": issue.line}
    if issue.column is not None:
        region["startColumn"] = issue.column
    if issue.end_line is not None:
        region["endLine"] = issue.end_line
    if issue.end_column is not None:
        region["endColumn"] = issue.end_column
    return region


def _result(issue: Issue, root: Path, *, baseline_state: str | None) -> dict[str, Any]:
    result: dict[str, Any] = {
        "ruleId": issue.rule,
        "level": _LEVEL[issue.severity],
        "message": {"text": issue.message},
        "locations": [
            {
                "physicalLocation": {
                    "artifactLocation": {
                        "uri": _relative_uri(issue.file, root),
                        "uriBaseId": "%SRCROOT%",
                    },
                    "region": _region(issue),
                }
            }
        ],
        "partialFingerprints": {FINGERPRINT_KEY: issue.fingerprint},
        "properties": {"package": issue.package},
    }
    if baseline_state is not None:
        result["baselineState"] = baseline_state
    return result


def build_sarif(
    *,
    new: list[Issue],
    known: list[Issue],
    ignored: list[Issue],
    tool_version: str,
    root: Path,
    baseline_used: bool,
) -> dict[str, Any]:
    results: list[dict[str, Any]] = []
    for issue in new:
        results.append(_result(issue, root, baseline_state="new" if baseline_used else None))
    for issue in known:
        results.append(_result(issue, root, baseline_state="unchanged"))
    for issue in ignored:
        suppressed = _result(issue, root, baseline_state=None)
        suppression: dict[str, Any] = {"kind": "inSource"}
        if issue.reason:
            suppression["justification"] = issue.reason
        suppressed["suppressions"] = [suppression]
        results.append(suppressed)

    return {
        "$schema": SARIF_SCHEMA,
        "version": SARIF_VERSION,
        "runs": [
            {
                "tool": {
                    "driver": {
                        "name": "spaghetti",
                        "version": tool_version,
                        "informationUri": INFORMATION_URI,
                        "rules": _rules_component(),
                    }
                },
                "originalUriBaseIds": {"%SRCROOT%": {"uri": root.resolve().as_uri() + "/"}},
                "results": results,
            }
        ],
    }
