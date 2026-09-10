"""Shared refs lookup and text rendering (CLI and MCP)."""

from __future__ import annotations

from dataclasses import dataclass, field

from lotsman.store import Store, SymbolRow

LINES_PER_FILE = 6  # enough to jump to the sites; the file itself has the rest
MAX_DEFS_SHOWN = 8


@dataclass
class RefSite:
    path: str
    count: int
    lines: list[int]
    same_line: bool = True  # qualified lookups: all parts on one line


@dataclass
class RefsResult:
    parts: list[str]
    defs: list[SymbolRow]
    refs: list[RefSite] = field(default_factory=list)

    @property
    def def_files(self) -> int:
        return len({row.path for row in self.defs})


def collect_refs(store: Store, name: str) -> RefsResult | None:
    """Definitions and referencing files (defining files excluded).

    `A.B` narrows definitions of `B` to files that define `A` (if any) and
    keeps files mentioning every part; files where the parts share a
    line (`ExLog.Info(...)`) come first with those lines, files that mention
    them only apart follow with `same_line=False` — weaker evidence."""
    parts = [part for part in name.split(".") if part]
    if not parts:
        return None
    member = parts[-1]
    defs = store.symbols_named(member)
    if len(parts) >= 2:
        # `ExLog.Info`: the `Info` defined next to `ExLog`, when there is one.
        owner_files = {row.path for row in store.symbols_named(parts[-2])}
        defs = [row for row in defs if row.path in owner_files] or defs
    def_paths = {row.path for row in defs}
    result = RefsResult(parts, defs)
    if len(parts) == 1:
        result.refs = [RefSite(path, count, lines)
                       for path, count, lines in store.reference_sites(member)
                       if path not in def_paths]
    else:
        files = {path for path, _ in store.files_referencing_all(parts)} - def_paths
        lines_by_part = []
        for part in parts:
            lines_by_part.append({path: (count, set(lines))
                                  for path, count, lines in store.reference_sites(part)
                                  if path in files})
        strong, weak = [], []
        for path in files:
            count, member_lines = lines_by_part[-1][path]
            shared = set(member_lines)
            for by_path in lines_by_part[:-1]:
                shared &= by_path[path][1]
            if shared:
                strong.append(RefSite(path, len(shared), sorted(shared)))
            else:
                weak.append(RefSite(path, count, sorted(member_lines), False))
        order = lambda site: (-site.count, site.path)  # noqa: E731
        result.refs = sorted(strong, key=order) + sorted(weak, key=order)
    if not defs and not result.refs:
        return None
    return result


def _site_line(site: RefSite) -> str:
    if not site.lines:  # pattern-only hit (wiring) without positions
        return f"  {site.path}  ({site.count}x)"
    shown = ",".join(map(str, site.lines[:LINES_PER_FILE]))
    more = len(site.lines) - LINES_PER_FILE
    return f"  {site.path}:{shown}" + (f" (+{more})" if more > 0 else "")


def render_refs(store: Store, name: str, limit: int = 20) -> str:
    result = collect_refs(store, name)
    if result is None:
        return f"(`{name}` not found in index)"
    parts, defs = result.parts, result.defs
    lines: list[str] = []
    if defs:
        lines.append("defined in:")
        lines += [f"  {row.path}:{row.line}  [{row.kind}] {row.signature}"
                  for row in defs[:MAX_DEFS_SHOWN]]
        if len(defs) > MAX_DEFS_SHOWN:
            lines.append(f"  … {len(defs) - MAX_DEFS_SHOWN} more "
                         f"(`defs {parts[-1]}` lists all)")
    if result.def_files > 1:
        hint = ("" if len(parts) > 1 else
                f"; qualify as `Type.{parts[-1]}` to narrow")
        lines.append(f"note: {result.def_files} files define `{parts[-1]}` — "
                     f"references below match the name and may belong to "
                     f"any of them{hint}")
    if not result.refs:
        return "\n".join(lines)

    strong = [s for s in result.refs if s.same_line]
    weak = [s for s in result.refs if not s.same_line]
    if len(parts) >= 2:
        names = " and ".join(f"`{part}`" for part in parts)
        head = (f"referenced by {len(strong)} files ({names} on one line; "
                "name-based matching, no type resolution):")
    else:
        head = (f"referenced by {len(strong)} files, "
                f"{sum(len(s.lines) or 1 for s in strong)} lines "
                "(name-based matching, no type resolution):")
    shown = 0
    if strong:
        lines.append(head)
        for site in strong[:limit]:
            lines.append(_site_line(site))
        shown = min(len(strong), limit)
    if weak and shown < limit:
        lines.append(f"weaker — {len(weak)} files mention "
                     f"{' and '.join(f'`{p}`' for p in parts)} only on "
                     "different lines:")
        for site in weak[:limit - shown]:
            lines.append(_site_line(site))
        shown += min(len(weak), limit - shown)
    hidden = len(result.refs) - shown
    if hidden > 0:
        lines.append(f"  … {hidden} more files (raise the limit to list them)")
    return "\n".join(lines)
