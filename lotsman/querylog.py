"""Opt-in query telemetry (env LOTSMAN_QUERYLOG=1). Local file only;
nothing leaves the machine."""

from __future__ import annotations

import json
import os
import time
from collections import Counter
from pathlib import Path

LOG_REL = ".lotsman/querylog.jsonl"


def enabled() -> bool:
    return os.environ.get("LOTSMAN_QUERYLOG") == "1"


def log(root: Path, cmd: str, args: dict, result: str) -> None:
    if not enabled():
        return
    path = root / LOG_REL
    row = {
        "ts": time.time(),
        "cmd": cmd,
        "args": args,
        "size": len(result),
        "empty": result.startswith("("),
    }
    try:
        path.parent.mkdir(exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    except OSError:
        pass


def summarize(root: Path) -> str:
    path = root / LOG_REL
    if not path.exists():
        return "(no query log; run the MCP server with LOTSMAN_QUERYLOG=1)"
    by_cmd: Counter[str] = Counter()
    empty: Counter[str] = Counter()
    queries: Counter[str] = Counter()
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        cmd = row.get("cmd", "?")
        by_cmd[cmd] += 1
        if row.get("empty"):
            empty[cmd] += 1
        query = (row.get("args") or {}).get("query")
        if query:
            queries[query] += 1
    lines = ["query log summary:"]
    for cmd, count in by_cmd.most_common():
        lines.append(f"  {cmd:10} {count:5} calls, {empty.get(cmd, 0)} empty")
    if queries:
        lines.append("top search queries:")
        lines.extend(f"  {count:3}x {query}"
                     for query, count in queries.most_common(10))
    return "\n".join(lines)
