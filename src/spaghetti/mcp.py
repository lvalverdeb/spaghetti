"""``spaghetti mcp`` — a Model Context Protocol server over stdio.

Coding agents ask "what should I fix in this package and how" and get the
report, the remediation plan and the fix patterns as structured data. The
transport is MCP's stdio one (newline-delimited JSON-RPC 2.0), implemented
directly: the protocol surface a tool server needs is five methods, and a
dependency-free implementation is what the Rust port mirrors byte for byte.

Every scan tool runs the CLI in a child process. That keeps the long-lived
server free of the settings-to-module-global mutation ``apply_settings`` does,
so two calls with different projects cannot leak into each other, and it is the
same code path users run by hand.
"""

from __future__ import annotations

import json
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any, TextIO

import spaghetti.config as _cfg
from spaghetti.rule_docs import RULE_DOCS
from spaghetti.rules import RULE_CATALOG
from spaghetti.settings import threshold_names

__all__ = ["PROTOCOL_VERSION", "TOOLS", "call_tool", "explain_rule", "serve"]

PROTOCOL_VERSION = "2025-06-18"
Runner = Callable[[list[str], Path], subprocess.CompletedProcess[str]]

_PATH = {"type": "string", "description": "Project directory (default: the server's cwd)"}
_PACKAGES = {
    "type": "object",
    "additionalProperties": {"type": "string"},
    "description": (
        "Packages to scan as {name: path}; omitted, the project's [tool.spaghetti] "
        "registry or auto-discovery applies"
    ),
}

TOOLS: list[dict[str, Any]] = [
    {
        "name": "scan",
        "description": (
            "Scan Python packages for structural smells. Returns the JSON report: per-package "
            "grade and score, every finding with file, line, severity, rule, message and "
            "fingerprint."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": _PATH,
                "packages": _PACKAGES,
                "severity": {
                    "type": "string",
                    "enum": ["info", "warning", "error"],
                    "description": "Minimum severity to include (default: info)",
                },
            },
        },
    },
    {
        "name": "plan",
        "description": (
            "Prioritised remediation plan: rules ranked by severity × fix effort (× git churn "
            "with hotspots=true), each with its affected files, a fix pattern and the full "
            "finding list. Start here to decide what to fix first."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": _PATH,
                "packages": _PACKAGES,
                "top": {"type": "integer", "minimum": 1, "description": "Steps to return"},
                "hotspots": {
                    "type": "boolean",
                    "description": "Weight by git change frequency (needs a git checkout)",
                },
            },
        },
    },
    {
        "name": "explain_rule",
        "description": (
            "What a rule catches, why it matters, the refactoring that fixes it, its "
            "thresholds and the Pylint/Ruff/SonarQube equivalents."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {"rule": {"type": "string", "description": "Rule id"}},
            "required": ["rule"],
        },
    },
    {
        "name": "compare",
        "description": (
            "Delta against a stored snapshot: score change, findings introduced and fixed, "
            "files that got worse. ref is 'latest', '~N', a git SHA prefix or a timestamp "
            "prefix."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": _PATH,
                "packages": _PACKAGES,
                "ref": {"type": "string", "description": "Snapshot reference"},
                "store": {"type": "string", "description": "Snapshot store path"},
            },
            "required": ["ref"],
        },
    },
    {
        "name": "baseline",
        "description": (
            "Baseline management. action='status' reports how many findings the baseline "
            "hides and which it lists as fixed; action='update' rewrites it from the current "
            "scan so only new findings fail CI from now on."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": _PATH,
                "packages": _PACKAGES,
                "action": {"type": "string", "enum": ["status", "update"]},
                "baseline": {"type": "string", "description": "Baseline file path"},
            },
            "required": ["action"],
        },
    },
]


class ToolError(ValueError):
    """A tool call failed; the message is for the agent."""


def _run_cli(argv: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "spaghetti.detector", *argv],
        cwd=cwd,
        capture_output=True,
        text=True,
    )


def _common_argv(args: dict[str, Any]) -> tuple[list[str], Path]:
    cwd = Path(args.get("path") or Path.cwd())
    if not cwd.is_dir():
        raise ToolError(f"path is not a directory: {cwd}")
    argv = ["--fail-on", "never"]
    packages = args.get("packages") or {}
    if not isinstance(packages, dict):
        raise ToolError("packages must be an object of {name: path}")
    for name, path in packages.items():
        argv += ["--package", f"{name}={path}"]
    return argv, cwd


def _last_stderr_line(proc: subprocess.CompletedProcess[str]) -> str:
    lines = proc.stderr.strip().splitlines()
    return lines[-1] if lines else ""


def _required(args: dict[str, Any], key: str) -> str:
    value = args.get(key)
    if not isinstance(value, str) or not value:
        raise ToolError(f"missing required argument '{key}'")
    return value


def _int_arg(args: dict[str, Any], key: str) -> int:
    value = args[key]
    if isinstance(value, bool) or not isinstance(value, int):
        raise ToolError(f"'{key}' must be an integer")
    return value


def _json_from(proc: subprocess.CompletedProcess[str]) -> dict[str, Any]:
    if proc.returncode == 2 and not proc.stdout.strip():
        raise ToolError(_last_stderr_line(proc) or "usage error")
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise ToolError(f"spaghetti produced no JSON report: {proc.stderr.strip()[-500:]}") from exc


def explain_rule(rule: str) -> dict[str, Any]:
    meta = RULE_CATALOG.get(rule)
    if meta is None:
        raise ToolError(f"unknown rule '{rule}'; known: {', '.join(sorted(RULE_CATALOG))}")
    doc = RULE_DOCS[rule]
    tunable = threshold_names()
    return {
        "id": meta.id,
        "summary": meta.summary,
        "default_severity": meta.default_severity,
        "tier": meta.tier,
        "enabled_by_default": meta.enabled_by_default,
        "why": doc.why,
        "fix": doc.fix,
        "equivalents": list(doc.equivalents),
        "thresholds": {
            (name.lower().replace("_", "-") if name in tunable else name): getattr(_cfg, name)
            for name in doc.thresholds
        },
        "suppress": f"# spaghetti-ignore[{meta.id}]: reason",
    }


def _scan(args: dict[str, Any], run: Runner) -> dict[str, Any]:
    argv, cwd = _common_argv(args)
    argv += ["--json"]
    if args.get("severity"):
        argv += ["--severity", str(args["severity"])]
    return _json_from(run(argv, cwd))


def _plan(args: dict[str, Any], run: Runner) -> dict[str, Any]:
    argv, cwd = _common_argv(args)
    argv += ["--plan", "--json"]
    if args.get("top") is not None:
        argv += ["--top", str(_int_arg(args, "top"))]
    if args.get("hotspots"):
        argv += ["--hotspots"]
    return _json_from(run(argv, cwd))


def _compare(args: dict[str, Any], run: Runner) -> dict[str, Any]:
    argv, cwd = _common_argv(args)
    argv += ["--json", "--compare", _required(args, "ref")]
    if args.get("store"):
        argv += ["--snapshot-store", str(args["store"])]
    return _json_from(run(argv, cwd))["compare"]


def _baseline(args: dict[str, Any], run: Runner) -> dict[str, Any]:
    argv, cwd = _common_argv(args)
    if args.get("baseline"):
        argv += ["--baseline", str(args["baseline"])]
    action = _required(args, "action")
    if action not in ("status", "update"):
        raise ToolError("action must be 'status' or 'update'")
    if action == "update":
        proc = run([*argv, "--update-baseline"], cwd)
        last_line = _last_stderr_line(proc)
        if proc.returncode != 0:
            raise ToolError(last_line or "failed")
        return {"updated": True, "message": last_line}
    report = _json_from(run([*argv, "--json"], cwd))
    return {"baseline": report.get("baseline"), "new_findings": len(report["issues"])}


def call_tool(name: str, args: dict[str, Any], run: Runner = _run_cli) -> dict[str, Any]:
    if name == "explain_rule":
        return explain_rule(str(args.get("rule", "")))
    handler = {"scan": _scan, "plan": _plan, "compare": _compare, "baseline": _baseline}.get(name)
    if handler is None:
        raise ToolError(f"unknown tool '{name}'")
    return handler(args, run)


# ── JSON-RPC over stdio ───────────────────────────────────────────────────────


def _result(id_: Any, result: Any) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": id_, "result": result}


def _error(id_: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": id_, "error": {"code": code, "message": message}}


def _tool_result(name: str, args: dict[str, Any], run: Runner) -> dict[str, Any]:
    try:
        payload = call_tool(name, args, run)
    except ToolError as exc:
        return {"content": [{"type": "text", "text": str(exc)}], "isError": True}
    return {
        "content": [{"type": "text", "text": json.dumps(payload, indent=2)}],
        "structuredContent": payload,
    }


def handle(message: object, *, version: str, run: Runner) -> dict[str, Any] | None:
    """One request → one response; notifications → ``None``. Never raises: a
    malformed message gets an error reply, and the server keeps serving."""
    if not isinstance(message, dict):
        return _error(None, -32600, "not a request object")
    method, id_ = message.get("method"), message.get("id")
    params = message.get("params")
    if params is None:
        params = {}
    if not isinstance(method, str):
        return _error(id_, -32600, "not a request")
    if not isinstance(params, dict):
        return _error(id_, -32602, "params must be an object")
    if method.startswith("notifications/"):
        return None
    if method == "initialize":
        return _result(
            id_,
            {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "spaghetti", "version": version},
                "instructions": (
                    "Call plan first: it ranks what to fix and includes the fix pattern per "
                    "rule. Use explain_rule for the reasoning behind a rule and scan for the "
                    "raw findings."
                ),
            },
        )
    if method == "ping":
        return _result(id_, {})
    if method == "tools/list":
        return _result(id_, {"tools": TOOLS})
    if method == "tools/call":
        name, args = params.get("name"), params.get("arguments") or {}
        if not isinstance(name, str) or not isinstance(args, dict):
            return _error(id_, -32602, "tools/call needs a name and an arguments object")
        return _result(id_, _tool_result(name, args, run))
    return _error(id_, -32601, f"method not found: {method}")


def serve(stdin: TextIO, stdout: TextIO, *, version: str, run: Runner = _run_cli) -> int:
    """Serve until EOF. Every request gets exactly one reply line."""
    for line in stdin:
        line = line.strip()
        if not line:
            continue
        try:
            message = json.loads(line)
        except json.JSONDecodeError:
            response: dict[str, Any] | None = _error(None, -32700, "parse error")
        else:
            response = handle(message, version=version, run=run)
        if response is not None:
            stdout.write(json.dumps(response) + "\n")
            stdout.flush()
    return 0
