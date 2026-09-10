"""Symbol slices: one full target body plus its local skeleton — enclosing
types and the nearest sibling signatures, not the whole file."""

from __future__ import annotations

from pathlib import Path

from lotsman.outlineview import outline_rows
from lotsman.store import TYPE_KINDS, Store, SymbolRow

# Signatures shown on each side of the target. The whole-file skeleton made a
# slice cost as much as outline + range read (measured on a Unity corpus:
# 3.5k vs 3.4k chars for one method) — `outline` exists for that.
NEIGHBORS = 3


def _attribute_start(lines: list[str], line: int) -> int:
    """Symbol lines point at the declaration; the body shown should still
    carry its `[Test]` / `@Override` lines."""
    while line > 1 and lines[line - 2].lstrip().startswith(("[", "@")):
        line -= 1
    return line


def _context_rows(rows: list[SymbolRow], targets: list[SymbolRow]) -> set[int]:
    keep: set[int] = set()
    target_ids = {id(t) for t in targets}
    for t in targets:
        outside = [i for i, r in enumerate(rows)
                   if id(r) not in target_ids and not t.line <= r.line <= t.end_line]
        for i, r in enumerate(rows):
            if (r.kind in TYPE_KINDS and id(r) not in target_ids
                    and r.line <= t.line and r.end_line >= t.end_line):
                keep.add(i)  # enclosing type
        keep.update([i for i in outside if rows[i].line < t.line][-NEIGHBORS:])
        keep.update([i for i in outside if rows[i].line > t.end_line][:NEIGHBORS])
    return keep


def generate_slice(store: Store, root: Path, path: str, name: str) -> str:
    rows = store.symbols_in_file(path)
    if not rows:
        return f"(no symbols indexed for {path})"

    targets = [row for row in rows if row.name == name]
    if not targets:
        return f"(no symbol `{name}` in {path}; try `outline {path}`)"

    try:
        text = (root / path).read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return f"(cannot read {path}: {exc})"

    lines = text.splitlines()
    shown = sum(row.end_line - row.line + 1 for row in targets)
    out = [f"{path}  (slice: {name} - {shown} of {len(lines)} lines shown)"]
    target_ids = {id(row) for row in targets}
    keep = _context_rows(rows, targets)
    covered_until = 0
    skeleton: list[SymbolRow] = []
    elided = hidden = 0

    def flush() -> None:
        nonlocal elided
        out.extend(outline_rows(skeleton, suffix=" ..."))
        skeleton.clear()
        if elided:
            out.append(f"      … {elided} symbols")
            elided = 0

    for i, row in enumerate(rows):
        if row.line <= covered_until:
            continue
        if id(row) in target_ids:
            flush()
            for n in range(_attribute_start(lines, row.line),
                           min(row.end_line, len(lines)) + 1):
                out.append(f"{n:5}: {lines[n - 1]}")
            covered_until = row.end_line
            continue
        if i in keep:
            if elided:
                flush()
            skeleton.append(row)
        else:
            if skeleton:
                flush()
            elided += 1
            hidden += 1
    flush()
    if hidden:
        out.append(f"(`outline {path}` lists all {len(rows)} symbols)")
    return "\n".join(out)
