"""The MCP server: protocol handling in-process, and the real stdio binary end to end."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from spaghetti.mcp import PROTOCOL_VERSION, TOOLS, call_tool, explain_rule, handle
from spaghetti.rules import RULE_CATALOG


def test_explain_rule_bundles_catalog_docs_and_live_thresholds():
    info = explain_rule("high-complexity")
    assert info["default_severity"] == "warning" and info["tier"] == "ast"
    assert info["thresholds"] == {"complexity-threshold": 10}
    assert "dispatch table" in info["fix"] and "Ruff C901 (mccabe)" in info["equivalents"]
    assert info["suppress"] == "# spaghetti-ignore[high-complexity]: reason"
    for rule in RULE_CATALOG:
        assert explain_rule(rule)["id"] == rule


def test_handle_initialize_list_and_errors():
    init = handle(
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}, version="9", run=None
    )
    assert init["result"]["protocolVersion"] == PROTOCOL_VERSION
    assert init["result"]["serverInfo"] == {"name": "spaghetti", "version": "9"}
    assert (
        handle({"jsonrpc": "2.0", "method": "notifications/initialized"}, version="9", run=None)
        is None
    )
    listed = handle({"jsonrpc": "2.0", "id": 2, "method": "tools/list"}, version="9", run=None)
    assert [t["name"] for t in listed["result"]["tools"]] == [t["name"] for t in TOOLS]
    assert {"scan", "plan", "explain_rule", "compare", "baseline"} == {t["name"] for t in TOOLS}
    missing = handle({"jsonrpc": "2.0", "id": 3, "method": "resources/list"}, version="9", run=None)
    assert missing["error"]["code"] == -32601
    bad = handle(
        {
            "jsonrpc": "2.0",
            "id": 4,
            "method": "tools/call",
            "params": {"name": "explain_rule", "arguments": {"rule": "nope"}},
        },
        version="9",
        run=None,
    )
    assert (
        bad["result"]["isError"] is True and "unknown rule" in bad["result"]["content"][0]["text"]
    )


def test_plan_tool_builds_the_cli_invocation(tmp_path: Path):
    seen: list[list[str]] = []

    def fake(argv, cwd):
        seen.append(argv)
        assert cwd == tmp_path
        return subprocess.CompletedProcess(
            argv, 0, stdout=json.dumps({"plan": [], "issues": []}), stderr=""
        )

    out = call_tool(
        "plan",
        {"path": str(tmp_path), "packages": {"p": "src/p"}, "top": 3, "hotspots": True},
        fake,
    )
    assert out == {"plan": [], "issues": []}
    assert seen == [
        [
            "--fail-on",
            "never",
            "--package",
            "p=src/p",
            "--plan",
            "--json",
            "--top",
            "3",
            "--hotspots",
        ]
    ]


def _rpc(id_: int, method: str, **params) -> str:
    return json.dumps({"jsonrpc": "2.0", "id": id_, "method": method, "params": params})


def test_stdio_server_end_to_end(tmp_path: Path):
    pkg = tmp_path / "src" / "pkg"
    pkg.mkdir(parents=True)
    (pkg / "m.py").write_text("def f(a, b, c, d, e, f, g, h):\n    return a\n")
    requests = "\n".join(
        [
            _rpc(1, "initialize", protocolVersion=PROTOCOL_VERSION, capabilities={}),
            json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}),
            _rpc(
                2,
                "tools/call",
                name="plan",
                arguments={"path": str(tmp_path), "packages": {"pkg": "src/pkg"}},
            ),
            _rpc(3, "tools/call", name="explain_rule", arguments={"rule": "too-many-params"}),
            "not json",
        ]
    )
    proc = subprocess.run(
        [sys.executable, "-m", "spaghetti.detector", "mcp"],
        input=requests + "\n",
        capture_output=True,
        text=True,
        cwd=tmp_path,
    )
    assert proc.returncode == 0, proc.stderr
    replies = [json.loads(line) for line in proc.stdout.splitlines()]
    assert [r.get("id") for r in replies] == [1, 2, 3, None]
    plan = replies[1]["result"]["structuredContent"]
    step = plan["plan"][0]
    assert step["rule"] == "too-many-params" and step["severity"] == "warning"
    assert step["files"][0].endswith("src/pkg/m.py") and "Parameter Object" in step["fix"]
    assert plan["issues"][0]["line"] == 1
    assert replies[2]["result"]["structuredContent"]["id"] == "too-many-params"
    assert replies[3]["error"]["code"] == -32700


def test_handle_never_raises_on_malformed_input():
    def boom(argv, cwd):  # pragma: no cover - must not be reached
        raise AssertionError("the CLI must not run for malformed calls")

    def call(name, arguments):
        msg = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments},
        }
        return handle(msg, version="9", run=boom)["result"]

    assert handle([], version="9", run=boom)["error"]["code"] == -32600
    assert handle({"id": 1, "method": 5}, version="9", run=boom)["error"]["code"] == -32600
    assert (
        handle({"id": 1, "method": "ping", "params": []}, version="9", run=boom)["error"]["code"]
        == -32602
    )
    assert "missing required argument 'ref'" in call("compare", {"path": "."})["content"][0]["text"]
    assert "missing required argument 'action'" in call("baseline", {})["content"][0]["text"]
    assert "action must be" in call("baseline", {"action": "wipe"})["content"][0]["text"]
    assert "'top' must be an integer" in call("plan", {"top": "three"})["content"][0]["text"]
