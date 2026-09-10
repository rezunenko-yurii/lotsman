"""Shared outline rendering (CLI, MCP, slice skeleton)."""

from __future__ import annotations

from lotsman.store import SymbolRow


# Member kinds whose consecutive runs collapse into one names-only line.
_COLLAPSIBLE = {"field": "fields", "event": "events"}


def outline_rows(rows: list[SymbolRow], suffix: str = "") -> list[str]:
    """One line per symbol; a run of 2+ consecutive fields (or events)
    collapses into one `[fields] a, b, c` line — a DTO with 200 fields must
    not cost more than reading the file (types are one range-read away)."""
    out: list[str] = []
    i = 0
    while i < len(rows):
        row = rows[i]
        j = i
        while (row.kind in _COLLAPSIBLE and j + 1 < len(rows)
               and rows[j + 1].kind == row.kind):
            j += 1
        if j > i:
            names = ", ".join(dict.fromkeys(r.name for r in rows[i:j + 1]))
            end = max(r.end_line for r in rows[i:j + 1])
            out.append(f"{row.line:5}-{end:<5} [{_COLLAPSIBLE[row.kind]}] "
                       f"{names}{suffix}")
        else:
            out.append(f"{row.line:5}-{row.end_line:<5} [{row.kind}] "
                       f"{row.signature}{suffix}")
        i = j + 1
    return out
