"""Incremental per-file cache: skip the per-file checks for unchanged files.

An entry is keyed by the SHA-1 of a file's text and lives under a directory
named by the *configuration key* — a hash of the applied settings, the tool
version and the Python minor version — so changing a threshold or upgrading
invalidates everything at once without a manifest. An entry holds exactly
what ``detector._scan_one_file`` produces before ``_finalize``: raw findings
(byte-offset columns, no fingerprints), the Maintainability Index, counts,
and the two workspace transports (raw imports, token stream). On a hit the
file is still parsed, because the package-level checks need the tree; the
profile shows parsing is cheap and the thirty-odd per-file walks are not.

Entries are written atomically (temp file + ``os.replace``) so worker
processes can share one directory, and any unreadable or mismatching entry is
a miss, never an error.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from spaghetti.checks.workspace_level import RawImport
from spaghetti.clones import TokenStream
from spaghetti.models import Issue

__all__ = ["CACHE_SCHEMA_VERSION", "DEFAULT_CACHE_DIR", "FileCache", "FileEntry", "cache_key"]

CACHE_SCHEMA_VERSION = 1
DEFAULT_CACHE_DIR = Path(".spaghetti/cache")


def cache_key(overrides: dict[str, Any], tool_version: str) -> str:
    """Hash of everything that changes a per-file result other than the file."""
    material = {
        "schema": CACHE_SCHEMA_VERSION,
        "tool": tool_version,
        "python": list(sys.version_info[:2]),
        "overrides": overrides,
    }
    return hashlib.sha1(
        json.dumps(material, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()[:16]


@dataclass
class FileEntry:
    issues: list[Issue]
    parsed: bool
    functions: int = 0
    mi: float | None = None
    raw_imports: list[RawImport] | None = None
    tokens: TokenStream | None = None

    def to_json(self) -> dict[str, Any]:
        return {
            "issues": [
                {
                    "rule": i.rule,
                    "line": i.line,
                    "severity": i.severity,
                    "message": i.message,
                    "column": i.column,
                    "end_line": i.end_line,
                    "end_column": i.end_column,
                }
                for i in self.issues
            ],
            "parsed": self.parsed,
            "functions": self.functions,
            "mi": self.mi,
            "raw_imports": (
                [[r.level, r.module, list(r.names), r.line] for r in self.raw_imports]
                if self.raw_imports is not None
                else None
            ),
            "tokens": list(self.tokens) if self.tokens is not None else None,
        }

    @classmethod
    def from_json(cls, data: dict[str, Any], *, file: Path, package: str) -> FileEntry:
        return cls(
            issues=[
                Issue(
                    file=file,
                    line=i["line"],
                    severity=i["severity"],
                    rule=i["rule"],
                    message=i["message"],
                    package=package,
                    column=i["column"],
                    end_line=i["end_line"],
                    end_column=i["end_column"],
                )
                for i in data["issues"]
            ],
            parsed=data["parsed"],
            functions=data["functions"],
            mi=data["mi"],
            raw_imports=(
                [RawImport(r[0], r[1], tuple(r[2]), r[3]) for r in data["raw_imports"]]
                if data["raw_imports"] is not None
                else None
            ),
            tokens=(tuple(data["tokens"]) if data["tokens"] is not None else None),  # type: ignore[arg-type]
        )


class FileCache:
    def __init__(self, root: Path, key: str) -> None:
        self.dir = root / key

    @staticmethod
    def _content_hash(source: str) -> str:
        return hashlib.sha1(source.encode("utf-8", errors="replace")).hexdigest()

    def _path(self, source: str) -> Path:
        return self.dir / f"{self._content_hash(source)}.json"

    def load(self, source: str, *, file: Path, package: str) -> FileEntry | None:
        try:
            data = json.loads(self._path(source).read_text(encoding="utf-8"))
            if data.get("schema_version") != CACHE_SCHEMA_VERSION:
                return None
            return FileEntry.from_json(data, file=file, package=package)
        except (OSError, ValueError, KeyError, TypeError, IndexError):
            return None

    def store(self, source: str, entry: FileEntry) -> None:
        try:
            self.dir.mkdir(parents=True, exist_ok=True)
            body = json.dumps({"schema_version": CACHE_SCHEMA_VERSION, **entry.to_json()})
            fd, tmp = tempfile.mkstemp(dir=self.dir, suffix=".tmp")
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(body)
            os.replace(tmp, self._path(source))
        except OSError:
            pass  # a cache that cannot be written is a cache that misses
