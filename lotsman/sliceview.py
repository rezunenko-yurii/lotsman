"""Symbol slices: one full target body plus a signature-only file skeleton."""

from __future__ import annotations

from pathlib import Path

from lotsman.store import Store


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
    covered_until = 0

    for row in rows:
        if row.line <= covered_until:
            continue
        if id(row) in target_ids:
            for i in range(row.line, min(row.end_line, len(lines)) + 1):
                out.append(f"{i:5}: {lines[i - 1]}")
            covered_until = row.end_line
            continue
        out.append(f"{row.line:5}-{row.end_line:<5} [{row.kind}] {row.signature} ...")

    return "\n".join(out)
