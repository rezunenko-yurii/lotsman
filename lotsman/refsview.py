"""Shared text rendering for the refs command (CLI and MCP)."""

from __future__ import annotations

from lotsman.store import Store


def render_refs(store: Store, name: str, limit: int = 20) -> str:
    parts = [part for part in name.split(".") if part]
    if not parts:
        return f"(`{name}` not found in index)"
    member = parts[-1]
    defs = store.symbols_named(member)
    if len(parts) >= 2:
        refs = store.files_referencing_all(parts)
        note = ("referenced by (files mentioning "
                + " and ".join(f"`{part}`" for part in parts)
                + "; name-based matching, no type resolution):")
    else:
        refs = store.files_referencing(member)
        note = "referenced by (name-based matching, no type resolution):"
    def_paths = {row.path for row in defs}
    ref_only = [(path, count) for path, count in refs if path not in def_paths]
    if not defs and not ref_only:
        return f"(`{name}` not found in index)"
    lines: list[str] = []
    if defs:
        lines.append("defined in:")
        lines += [f"  {row.path}:{row.line}  [{row.kind}] {row.signature}"
                  for row in defs]
    if ref_only:
        lines.append(note)
        lines += [f"  {path}  ({count}x)" for path, count in ref_only[:limit]]
    return "\n".join(lines)
