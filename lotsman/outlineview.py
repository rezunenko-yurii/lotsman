"""Shared outline rendering (CLI, MCP, slice skeleton)."""

from __future__ import annotations

from lotsman import csdefines, docs, unity
from lotsman.store import Store, SymbolRow


def outline_header(store: Store, path: str, rows: list[SymbolRow]) -> list[str]:
    """Context lines above an outline: for a doc, whether it is binding; for
    code, the Unity assembly compiling it and the nearest module docs (one
    line each)."""
    if path.endswith((".md", ".markdown")):
        if path in store.binding_docs():
            return ["  binding: an AGENTS.md requires reading this document in full"]
        return []
    out = []
    project = unity.load(store) if path.endswith(".cs") else None
    asm = unity.assembly_line(project, path) if project else None
    if asm:
        out.append(f"  {asm}")
    line = docs.docs_for(path, store.doc_paths() - {path})
    return out + ([f"  {line}"] if line else [])


# Member kinds whose consecutive runs collapse into one names-only line.
_COLLAPSIBLE = {"field": "fields", "event": "events"}


def condition_marks(store: Store, path: str, rows: list[SymbolRow]) -> list[str]:
    """Per row: ` [#if COND]` when the symbol sits under a C# `#if` branch
    whose condition differs from its enclosing symbol's (a class wrapped in
    `#if UNITY_EDITOR` is marked once, not on every member)."""
    if not path.endswith(".cs"):
        return [""] * len(rows)
    directives = store.directives_in_file(path)
    if not any(d.kind == "if" for d in directives):
        return [""] * len(rows)
    conds = [(r := csdefines.region_at(directives, row.line)) and r.cond for row in rows]
    marks = []
    for i, row in enumerate(rows):
        parent = next((j for j in range(i - 1, -1, -1)
                       if rows[j].line <= row.line <= rows[j].end_line), None)
        inherited = conds[parent] if parent is not None else None
        marks.append(f" [#if {conds[i]}]" if conds[i] and conds[i] != inherited else "")
    return marks


def outline_rows(rows: list[SymbolRow], suffix: str = "",
                 marks: list[str] | None = None) -> list[str]:
    """One line per symbol; a run of 2+ consecutive fields (or events)
    collapses into one `[fields] a, b, c` line — a DTO with 200 fields must
    not cost more than reading the file (types are one range-read away).
    `marks` (per row, see condition_marks) end the line."""
    marks = marks or [""] * len(rows)
    out: list[str] = []
    i = 0
    while i < len(rows):
        row = rows[i]
        j = i
        while (row.kind in _COLLAPSIBLE and j + 1 < len(rows)
               and rows[j + 1].kind == row.kind and marks[j + 1] == marks[i]):
            j += 1
        if j > i:
            names = ", ".join(dict.fromkeys(r.name for r in rows[i:j + 1]))
            end = max(r.end_line for r in rows[i:j + 1])
            out.append(f"{row.line:5}-{end:<5} [{_COLLAPSIBLE[row.kind]}] "
                       f"{names}{suffix}{marks[i]}")
        else:
            out.append(f"{row.line:5}-{row.end_line:<5} [{row.kind}] "
                       f"{row.signature}{suffix}{marks[i]}")
        i = j + 1
    return out
