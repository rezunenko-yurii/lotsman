"""MCP server tests: dispatch, error paths, and protocol fixtures over stdio."""

import json
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

from helpers import FixtureRepoMixin
from lotsman.mcp_server import TOOLS, McpServer

REPO_ROOT = Path(__file__).resolve().parent.parent


class TestMcpDispatch(FixtureRepoMixin, unittest.TestCase):
    def _server(self) -> McpServer:
        server = McpServer(self.tmp)
        server.store = self.store  # reuse fixture store
        return server

    def test_protocol_flow(self):
        server = self._server()
        init = server.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                              "params": {"protocolVersion": "2025-01-01"}})
        self.assertEqual(init["result"]["protocolVersion"], "2025-01-01")
        self.assertEqual(init["result"]["serverInfo"]["name"], "lotsman")

        self.assertIsNone(server.handle(
            {"jsonrpc": "2.0", "method": "notifications/initialized"}))

        tools = server.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        names = {t["name"] for t in tools["result"]["tools"]}
        self.assertEqual(names, {"map", "search", "outline", "defs", "refs",
                                 "impact"})

        call = server.handle({
            "jsonrpc": "2.0", "id": 3, "method": "tools/call",
            "params": {"name": "search",
                       "arguments": {"query": "prepare fuel", "mode": "bm25"}}})
        self.assertFalse(call["result"]["isError"])
        self.assertIn("pkg/core.py", call["result"]["content"][0]["text"])

        refs = server.handle({
            "jsonrpc": "2.0", "id": 4, "method": "tools/call",
            "params": {"name": "refs", "arguments": {"name": "prepare_fuel"}}})
        self.assertIn("referenced by", refs["result"]["content"][0]["text"])
        # confidence marker travels with the output
        self.assertIn("name-based matching", refs["result"]["content"][0]["text"])

    def test_errors(self):
        server = self._server()
        bad_method = server.handle({"jsonrpc": "2.0", "id": 1, "method": "nope"})
        self.assertEqual(bad_method["error"]["code"], -32601)

        bad_tool = server.handle({
            "jsonrpc": "2.0", "id": 2, "method": "tools/call",
            "params": {"name": "explode", "arguments": {}}})
        self.assertEqual(bad_tool["error"]["code"], -32602)

        # tool raising (missing required arg) -> isError result, not a crash
        broken = server.handle({
            "jsonrpc": "2.0", "id": 3, "method": "tools/call",
            "params": {"name": "outline", "arguments": {}}})
        self.assertTrue(broken["result"]["isError"])

    def test_output_truncation(self):
        server = self._server()
        with mock.patch("lotsman.mcp_server.MAX_OUTPUT_CHARS", 40):
            call = server.handle({
                "jsonrpc": "2.0", "id": 1, "method": "tools/call",
                "params": {"name": "outline",
                           "arguments": {"file": "pkg/core.py"}}})
        text = call["result"]["content"][0]["text"]
        self.assertIn("(truncated)", text)
        self.assertLess(len(text), 100)


class TestToolSchemas(unittest.TestCase):
    """Schema stability: agents bind to these shapes."""

    def test_every_tool_is_well_formed(self):
        for tool in TOOLS:
            with self.subTest(tool=tool["name"]):
                self.assertTrue(tool["description"].strip())
                schema = tool["inputSchema"]
                self.assertEqual(schema["type"], "object")
                props = schema.get("properties", {})
                for req in schema.get("required", []):
                    self.assertIn(req, props)

    def test_required_arguments_are_stable(self):
        required = {t["name"]: t["inputSchema"].get("required", [])
                    for t in TOOLS}
        self.assertEqual(required, {
            "map": [], "impact": [],
            "search": ["query"], "outline": ["file"],
            "defs": ["name"], "refs": ["name"],
        })


class TestStdioProtocol(FixtureRepoMixin, unittest.TestCase):
    def _run_server(self, stdin: str) -> list[dict]:
        proc = subprocess.run(
            [sys.executable, "-m", "lotsman", "--repo", str(self.tmp), "mcp"],
            input=stdin, capture_output=True, text=True, timeout=120,
            cwd=REPO_ROOT)
        return [json.loads(l) for l in proc.stdout.splitlines() if l.strip()]

    def test_stdio_roundtrip(self):
        msgs = "\n".join(json.dumps(m) for m in [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
             "params": {"name": "defs", "arguments": {"name": "Engine"}}},
        ]) + "\n"
        lines = self._run_server(msgs)
        self.assertEqual(len(lines), 2)  # notification gets no response
        self.assertEqual(lines[0]["id"], 1)
        self.assertIn("pkg/core.py:1", lines[1]["result"]["content"][0]["text"])

    def test_malformed_json_line(self):
        stdin = ("this is not json at all\n"
                 + json.dumps({"jsonrpc": "2.0", "id": 7,
                               "method": "initialize", "params": {}}) + "\n")
        lines = self._run_server(stdin)
        self.assertEqual(len(lines), 2)
        self.assertEqual(lines[0]["error"]["code"], -32700)  # parse error
        self.assertEqual(lines[1]["id"], 7)  # server survived and answered

    def test_stdout_is_pure_jsonrpc(self):
        # Every stdout line must parse as JSON — progress bars or prints
        # anywhere in the pipeline would corrupt the protocol stream.
        msgs = "\n".join(json.dumps(m) for m in [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
             "params": {"name": "map", "arguments": {"budget": 200}}},
        ]) + "\n"
        proc = subprocess.run(
            [sys.executable, "-m", "lotsman", "--repo", str(self.tmp), "mcp"],
            input=msgs, capture_output=True, text=True, timeout=120,
            cwd=REPO_ROOT)
        for line in proc.stdout.splitlines():
            if line.strip():
                json.loads(line)  # raises on any non-JSON noise


if __name__ == "__main__":
    unittest.main()
