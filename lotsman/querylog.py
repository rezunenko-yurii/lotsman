"""Opt-in query telemetry. Local file only; nothing leaves the machine.

Enabled by env `LOTSMAN_QUERYLOG=1` or by a flag file `.lotsman/querylog.on`
under the indexed root — the flag covers CLI calls too, whose environment an
agent's shell does not inherit from an MCP config. `LOTSMAN_QUERYLOG=0`
disables logging even when the flag exists.

The flag switches itself off so a measurement week cannot turn into forever:
its content may name the last day (`until 2026-09-17`); without a date it
expires FLAG_DEFAULT_DAYS after the flag was created."""

from __future__ import annotations

import datetime as dt
import json
import os
import re
import time
from collections import Counter
from pathlib import Path

LOG_REL = ".lotsman/querylog.jsonl"
FLAG_REL = ".lotsman/querylog.on"
FLAG_DEFAULT_DAYS = 14
_DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")


def flag_until(root: Path) -> dt.date | None:
    """Last logging day set by the flag file; None when there is no flag."""
    flag = root / FLAG_REL
    try:
        text = flag.read_text(encoding="utf-8", errors="replace")
        created = flag.stat().st_mtime
    except OSError:
        return None
    m = _DATE_RE.search(text)
    if m:
        try:
            return dt.date.fromisoformat(m.group(0))
        except ValueError:
            pass
    return (dt.date.fromtimestamp(created)
            + dt.timedelta(days=FLAG_DEFAULT_DAYS))


def enabled(root: Path | None = None, today: dt.date | None = None) -> bool:
    """`LOTSMAN_QUERYLOG=1` on, `=0` off (wins over the flag — benchmarks must
    not pollute a project's log), otherwise an unexpired flag file decides."""
    env = os.environ.get("LOTSMAN_QUERYLOG")
    if env in ("0", "1"):
        return env == "1"
    if root is None:
        return False
    until = flag_until(root)
    return until is not None and (today or dt.date.today()) <= until


def log(root: Path, cmd: str, args: dict, result: str, via: str = "mcp") -> None:
    if not enabled(root):
        return
    path = root / LOG_REL
    row = {
        "ts": time.time(),
        "cmd": cmd,
        "args": args,
        "size": len(result),
        "empty": result.startswith("("),
        "via": via,
    }
    try:
        path.parent.mkdir(exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    except OSError:
        pass


def _status(root: Path) -> str:
    until = flag_until(root)
    if until is None:
        return "logging: off (no flag)"
    state = "on" if dt.date.today() <= until else "expired"
    return f"logging: {state}, flag until {until.isoformat()}"


def summarize(root: Path) -> str:
    path = root / LOG_REL
    if not path.exists():
        return (f"(no query log; set LOTSMAN_QUERYLOG=1 for the MCP server or "
                f"create {FLAG_REL} (e.g. `until 2026-12-31`) to log CLI and "
                f"MCP calls; {_status(root)})")
    by_cmd: Counter[str] = Counter()
    empty: Counter[str] = Counter()
    queries: Counter[str] = Counter()
    via: Counter[str] = Counter()
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        cmd = row.get("cmd", "?")
        by_cmd[cmd] += 1
        via[row.get("via", "mcp")] += 1
        if row.get("empty"):
            empty[cmd] += 1
        query = (row.get("args") or {}).get("query")
        if query:
            queries[query] += 1
    lines = ["query log summary:",
             f"  {_status(root)}",
             "  via " + ", ".join(f"{name} {count}" for name, count in via.most_common())]
    for cmd, count in by_cmd.most_common():
        lines.append(f"  {cmd:10} {count:5} calls, {empty.get(cmd, 0)} empty")
    if queries:
        lines.append("top search queries:")
        lines.extend(f"  {count:3}x {query}"
                     for query, count in queries.most_common(10))
    return "\n".join(lines)
