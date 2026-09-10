"""MCP (Model Context Protocol) stdio server: exposes lotsman to AI agents as
typed tools instead of shell commands. stdlib-only JSON-RPC implementation —
newline-delimited JSON over stdin/stdout, protocol subset: initialize,
tools/list, tools/call, ping.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

from lotsman import (__version__, embed, extract, indexer, outlineview,
                     querylog, refsview, repomap, search as search_mod,
                     sliceview)

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
            "symbols, plus the best Markdown doc sections (path:start-end). "
            "Use instead of grep when looking for code or docs by meaning, "
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
        "name": "slice",
        "description": (
            "Full body of one symbol plus its local skeleton: enclosing "
            "types and the nearest sibling signatures (use outline for the "
            "whole file). Use instead of reading a whole file when one "
            "symbol matters."),
        "inputSchema": {
            "type": "object",
            "properties": {
                "file": {"type": "string", "description": "repo-relative path"},
                "name": {"type": "string", "description": "symbol name"},
            },
            "required": ["file", "name"],
        },
    },
    {
        "name": "impact",
        "description": (
            "Heuristic impact map: given changed files (or auto-detected recent "
            "changes), show their symbols and the files that reference those "
            "symbols by name, ranked by usage. Name-based matching without type "
            "resolution — a navigation aid, not a compiler-grade dependency "
            "graph. Call BEFORE editing a shared file and AFTER a batch of "
            "edits to know what to re-check."),
        "inputSchema": {
            "type": "object",
            "properties": {
                "files": {"type": "array", "items": {"type": "string"},
                          "description": "repo-relative changed files; empty = "
                                         "auto-detect"},
                "since_hours": {"type": "number",
                                "description": "detection window when no git "
                                               "(default 24)"},
                "budget": {"type": "integer",
                           "description": "token budget for the report "
                                          "(default 1500; raise if truncated)"},
                "tests": {"type": "boolean",
                          "description": "show only impacted test files"},
            },
        },
    },
    {
        "name": "change",
        "description": ("Before changing a symbol (signature, rename, removal): "
                        "definition, declarations it implements, overrides, and "
                        "callers file:line in files naming its type family "
                        "(tests marked); unrelated same-name uses only counted. "
                        "Name-based."),
        "inputSchema": {
            "type": "object",
            "properties": {"target": {"type": "string",
                                      "description": "Type.Member, Member or Type"},
                           "limit": {"type": "integer", "description": "default 25"}},
            "required": ["target"],
        },
    },
    {
        "name": "unity",
        "description": ("Unity project facts: the assembly (asmdef) compiling a "
                        "file or type, its references and who references it, "
                        "targets it builds for, #if branches per platform/Editor; "
                        "with `uses`, whether that code can see another type. "
                        "target `errors`: compile errors from the Editor log, "
                        "flagged when edits are newer than the log."),
        "inputSchema": {
            "type": "object",
            "properties": {"target": {"type": "string",
                                      "description": "file, type, assembly or `errors`"},
                           "log": {"type": "string", "description": "Editor log path"},
                           "uses": {"type": "string",
                                    "description": "type/file/assembly to check visibility of"}},
            "required": ["target"],
        },
    },
    {
        "name": "refs",
        "description": ("Who uses a symbol: definitions plus referencing "
                        "files with line numbers (`path:12,40`). `Type.Member` "
                        "narrows to lines naming both. Name-based: flags names "
                        "defined in several files. Use before changing a "
                        "signature."),
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
        self._degraded: dict[str, str] = {}

    def _refresh(self) -> None:
        """Keep the index fresh; throttled, incremental, so nearly free."""
        now = time.monotonic()
        if now - self._last_refresh < REFRESH_INTERVAL:
            return
        self._degraded = indexer.index_repo(self.root, self.store).degraded
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
        doc_hits = search_mod.search_docs(self.store, args["query"], root=self.root)
        if not hits and not doc_hits:
            return "(no results)"
        lines = [f"{h.symbol.path}:{h.symbol.line}  [{h.symbol.kind}] {h.symbol.signature}"
                 for h in hits]
        lines += search_mod.render_doc_hits(doc_hits, self.store.binding_docs())
        return "\n".join(lines)

    def _tool_outline(self, args: dict) -> str:
        rows = self.store.symbols_in_file(args["file"])
        if not rows:
            return f"(no symbols indexed for {args['file']})"
        return "\n".join([f"{args['file']}:",
                          *outlineview.outline_header(self.store, args["file"], rows),
                          *outlineview.outline_rows(rows, marks=outlineview.condition_marks(
                              self.store, args["file"], rows))])

    def _tool_defs(self, args: dict) -> str:
        rows = self.store.symbols_named(args["name"])
        if not rows:
            return f"(no definitions of `{args['name']}`)"
        return "\n".join(
            f"{r.path}:{r.line}  [{r.kind}] {r.signature}" for r in rows)

    def _tool_slice(self, args: dict) -> str:
        return sliceview.generate_slice(
            self.store, self.root, args["file"], args["name"])

    def _tool_impact(self, args: dict) -> str:
        from lotsman import impact
        files = list(args.get("files") or [])
        if files:
            changed, method = files, "explicit"
        else:
            changed, method = impact.detect_changed(
                self.root, self.store,
                float(args.get("since_hours") or impact.DEFAULT_SINCE_HOURS))
        budget = int(args.get("budget") or impact.DEFAULT_BUDGET)
        tests = bool(args.get("tests"))
        return (impact.generate_impact(self.store, changed, budget=budget,
                                       tests_only=tests, root=self.root)
                + f"\n(change detection: {method})")

    def _tool_change(self, args: dict) -> str:
        from lotsman import changeview
        return changeview.render_change(self.store, args["target"],
                                        int(args.get("limit") or 25))

    def _tool_unity(self, args: dict) -> str:
        from lotsman import unity
        return unity.render_unity(self.store, args["target"], args.get("uses"),
                                  root=self.root, log=args.get("log"))

    def _tool_refs(self, args: dict) -> str:
        return refsview.render_refs(
            self.store, args["name"], int(args.get("limit") or 20))

    # --- JSON-RPC dispatch ---------------------------------------------------

    def handle(self, msg: dict) -> dict | None:
        method = msg.get("method")
        msg_id = msg.get("id")
        if method == "initialize":
            return self._result(msg_id, {
                "protocolVersion": msg.get("params", {}).get(
                    "protocolVersion", PROTOCOL_VERSION),
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "lotsman", "version": __version__},
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
            "slice": self._tool_slice,
            "refs": self._tool_refs,
            "change": self._tool_change,
            "unity": self._tool_unity,
            "impact": self._tool_impact,
        }.get(name)
        if fn is None:
            text = f"unknown tool: {name}"
            querylog.log(self.root, name or "?", args, text)
            return self._error(msg_id, -32602, f"unknown tool: {name}")
        try:
            self._refresh()
            text = fn(args)
        except Exception as e:
            text = f"{type(e).__name__}: {e}"
            querylog.log(self.root, name or "?", args, text)
            return self._result(msg_id, {
                "content": [{"type": "text", "text": text}],
                "isError": True,
            })
        querylog.log(self.root, name, args, text)
        if len(text) > MAX_OUTPUT_CHARS:
            text = text[:MAX_OUTPUT_CHARS] + "\n… (truncated)"
        if self._degraded:  # agents read tool output, not the server's stderr
            text += "\n" + extract.degraded_warning(self._degraded)
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
    import os
    # stdout belongs to JSON-RPC exclusively; keep third-party progress bars
    # (HF hub model download on first run) off it.
    os.environ.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")
    os.environ.setdefault("TQDM_DISABLE", "1")
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
