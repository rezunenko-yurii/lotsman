"""MCP (Model Context Protocol) stdio server: exposes codemap to AI agents as
typed tools instead of shell commands. stdlib-only JSON-RPC implementation —
newline-delimited JSON over stdin/stdout, protocol subset: initialize,
tools/list, tools/call, ping.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

from codemap import __version__, embed, indexer, repomap, search as search_mod

PROTOCOL_VERSION = "2024-11-05"
REFRESH_INTERVAL = 10.0  # seconds between incremental reindex checks
MAX_OUTPUT_CHARS = 40_000  # hard cap per tool response

TOOLS = [
    {
        "name": "map",
        "description": (
            "Token-budgeted map of the repository's most important symbols "
            "(PageRank over the reference graph). Call this FIRST when starting "
            "work in an unfamiliar area. Pass identifiers relevant to the task "
            "as `mentions` to bias the map toward them; pass files already in "
            "context as `focus` to see their dependencies instead of them."),
        "inputSchema": {
            "type": "object",
            "properties": {
                "budget": {"type": "integer",
                           "description": "token budget (default 2048)"},
                "focus": {"type": "array", "items": {"type": "string"},
                          "description": "repo-relative files already in context"},
                "mentions": {"type": "array", "items": {"type": "string"},
                             "description": "identifiers relevant to the task"},
            },
        },
    },
    {
        "name": "search",
        "description": (
            "Hybrid (BM25 + semantic vector, RRF-fused) search over all "
            "symbols. Use instead of grep when looking for code by meaning, "
            "e.g. 'retry backoff logic'. Returns path:line with signatures."),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "limit": {"type": "integer", "description": "default 10"},
                "mode": {"type": "string",
                         "enum": ["auto", "hybrid", "bm25", "vector"]},
            },
            "required": ["query"],
        },
    },
    {
        "name": "outline",
        "description": (
            "Symbol skeleton of one file (classes/functions with line ranges "
            "and signatures). Use instead of reading a file to see what is "
            "inside; then read only the relevant line range."),
        "inputSchema": {
            "type": "object",
            "properties": {"file": {"type": "string",
                                    "description": "repo-relative path"}},
            "required": ["file"],
        },
    },
    {
        "name": "defs",
        "description": "Where a symbol is defined: path:line + signature for each definition.",
        "inputSchema": {
            "type": "object",
            "properties": {"name": {"type": "string"}},
            "required": ["name"],
        },
    },
    {
        "name": "refs",
        "description": ("Who uses a symbol: definitions plus referencing files "
                        "with use counts. Use before changing a signature."),
        "inputSchema": {
            "type": "object",
            "properties": {"name": {"type": "string"},
                           "limit": {"type": "integer", "description": "default 20"}},
            "required": ["name"],
        },
    },
]


class McpServer:
    def __init__(self, root: Path):
        self.root = root
        self.store = indexer.open_store(root)
        self._last_refresh = 0.0

    def _refresh(self) -> None:
        """Keep the index fresh; throttled, incremental, so nearly free."""
        now = time.monotonic()
        if now - self._last_refresh < REFRESH_INTERVAL:
            return
        indexer.index_repo(self.root, self.store)
        embed.embed_missing(self.store)
        self._last_refresh = now

    # --- tool implementations (return plain text) ---------------------------

    def _tool_map(self, args: dict) -> str:
        return repomap.generate_map(
            self.store,
            budget=int(args.get("budget") or repomap.DEFAULT_BUDGET),
            focus=set(args.get("focus") or []),
            mentions=set(args.get("mentions") or []))

    def _tool_search(self, args: dict) -> str:
        hits = search_mod.search(
            self.store, args["query"],
            limit=int(args.get("limit") or 10),
            mode=args.get("mode") or "auto")
        if not hits:
            return "(no results)"
        return "\n".join(
            f"{h.symbol.path}:{h.symbol.line}  [{h.symbol.kind}] {h.symbol.signature}"
            for h in hits)

    def _tool_outline(self, args: dict) -> str:
        rows = self.store.symbols_in_file(args["file"])
        if not rows:
            return f"(no symbols indexed for {args['file']})"
        lines = [f"{args['file']}:"]
        lines += [f"{r.line:5}-{r.end_line:<5} [{r.kind}] {r.signature}" for r in rows]
        return "\n".join(lines)

    def _tool_defs(self, args: dict) -> str:
        rows = self.store.symbols_named(args["name"])
        if not rows:
            return f"(no definitions of `{args['name']}`)"
        return "\n".join(
            f"{r.path}:{r.line}  [{r.kind}] {r.signature}" for r in rows)

    def _tool_refs(self, args: dict) -> str:
        name = args["name"]
        limit = int(args.get("limit") or 20)
        defs = self.store.symbols_named(name)
        refs = self.store.files_referencing(name)
        def_paths = {d.path for d in defs}
        ref_only = [(p, c) for p, c in refs if p not in def_paths]
        if not defs and not ref_only:
            return f"(`{name}` not found in index)"
        lines = []
        if defs:
            lines.append("defined in:")
            lines += [f"  {d.path}:{d.line}  [{d.kind}] {d.signature}" for d in defs]
        if ref_only:
            lines.append("referenced by:")
            lines += [f"  {p}  ({c}x)" for p, c in ref_only[:limit]]
        return "\n".join(lines)

    # --- JSON-RPC dispatch ---------------------------------------------------

    def handle(self, msg: dict) -> dict | None:
        method = msg.get("method")
        msg_id = msg.get("id")
        if method == "initialize":
            return self._result(msg_id, {
                "protocolVersion": msg.get("params", {}).get(
                    "protocolVersion", PROTOCOL_VERSION),
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "codemap", "version": __version__},
            })
        if method in ("notifications/initialized", "notifications/cancelled"):
            return None  # notifications get no response
        if method == "ping":
            return self._result(msg_id, {})
        if method == "tools/list":
            return self._result(msg_id, {"tools": TOOLS})
        if method == "tools/call":
            return self._call_tool(msg_id, msg.get("params", {}))
        if msg_id is None:
            return None  # unknown notification — ignore
        return self._error(msg_id, -32601, f"method not found: {method}")

    def _call_tool(self, msg_id, params: dict) -> dict:
        name = params.get("name")
        args = params.get("arguments") or {}
        fn = {
            "map": self._tool_map,
            "search": self._tool_search,
            "outline": self._tool_outline,
            "defs": self._tool_defs,
            "refs": self._tool_refs,
        }.get(name)
        if fn is None:
            return self._error(msg_id, -32602, f"unknown tool: {name}")
        try:
            self._refresh()
            text = fn(args)
        except Exception as e:
            return self._result(msg_id, {
                "content": [{"type": "text", "text": f"{type(e).__name__}: {e}"}],
                "isError": True,
            })
        if len(text) > MAX_OUTPUT_CHARS:
            text = text[:MAX_OUTPUT_CHARS] + "\n… (truncated)"
        return self._result(msg_id, {
            "content": [{"type": "text", "text": text}],
            "isError": False,
        })

    @staticmethod
    def _result(msg_id, result: dict) -> dict:
        return {"jsonrpc": "2.0", "id": msg_id, "result": result}

    @staticmethod
    def _error(msg_id, code: int, message: str) -> dict:
        return {"jsonrpc": "2.0", "id": msg_id,
                "error": {"code": code, "message": message}}


def serve(root: Path) -> int:
    """Blocking stdio loop: one JSON message per line."""
    server = McpServer(root)
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            print(json.dumps({"jsonrpc": "2.0", "id": None,
                              "error": {"code": -32700, "message": "parse error"}}),
                  flush=True)
            continue
        response = server.handle(msg)
        if response is not None:
            print(json.dumps(response, ensure_ascii=False), flush=True)
    server.store.close()
    return 0
