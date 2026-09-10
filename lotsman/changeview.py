"""`change TARGET`: one capped report of what a change to a symbol touches.

Definition, declarations it implements, overrides/implementations below it,
and callers with `file:line` — but only in files that name the target's type
family (the type, its supertypes, its subtypes). A member name used on an
unrelated type (`dict.TryGetValue`) is counted, not listed: `impact` listed
30-44 such files per question on a Unity corpus. Name-based throughout.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from lotsman import unity
from lotsman.refsview import RefSite, _site_line, is_data_path
from lotsman.store import TYPE_KINDS, Store, SymbolRow, enclosing_types
from lotsman.textutil import is_test_path

MAX_FAMILY_DEPTH = 6
MAX_FAMILY_TYPES = 200
CALLERS_SHOWN = 25
IMPLS_SHOWN = 20
NOT_COVERED = "asset (prefab/scene) references and reflection by string name"


@dataclass
class Impl:
    type_name: str
    row: SymbolRow


@dataclass
class ChangeReport:
    target: str
    owner: str | None                  # type the member belongs to
    member: str | None                 # None: the target is a type
    defs: list[SymbolRow] = field(default_factory=list)
    declared_by: list[Impl] = field(default_factory=list)   # in supertypes
    impls: list[Impl] = field(default_factory=list)         # in subtypes
    subtypes: list[tuple[str, str, int]] = field(default_factory=list)  # type, path, line
    callers: list[RefSite] = field(default_factory=list)
    data: list[RefSite] = field(default_factory=list)  # configs; same_line = type named
    name_only: int = 0
    extension: bool = False
    ambiguous_owners: list[str] = field(default_factory=list)


def _owned(store: Store, path: str, name: str, owner: str) -> list[SymbolRow]:
    """Symbols `name` in `path` whose innermost enclosing type is `owner`."""
    rows = store.symbols_in_file(path)
    owners = enclosing_types([(r.name, r.kind, r.line, r.end_line) for r in rows],
                             ("method", "field", "event", "function"))
    if owner not in owners.get(name, set()):
        return []
    types = [(r.name, r.line, r.end_line) for r in rows if r.kind in TYPE_KINDS]
    out = []
    for r in rows:
        if r.name != name or r.kind in TYPE_KINDS:
            continue
        enclosing = [t for t in types if t[1] <= r.line <= t[2]]
        if enclosing and max(enclosing, key=lambda t: t[1])[0] == owner:
            out.append(r)
    return out


def _type_paths(store: Store, name: str) -> list[str]:
    return sorted({r.path for r in store.symbols_named(name) if r.kind in TYPE_KINDS})


def _family(store: Store, owner: str) -> tuple[set[str], list[tuple[str, str, int]]]:
    """(transitive supertypes, transitive subtypes as (type, path, line))."""
    supers: set[str] = set()
    frontier = {owner}
    for _ in range(MAX_FAMILY_DEPTH):
        nxt = {b for _, b in store.supertypes(frontier)} - supers - {owner}
        if not nxt:
            break
        supers |= nxt
        frontier = nxt
    subs: list[tuple[str, str, int]] = []
    seen = {owner}
    frontier = {owner}
    for _ in range(MAX_FAMILY_DEPTH):
        rows = [r for r in store.subtypes(frontier) if r[0] not in seen]
        if not rows:
            break
        for type_name, _, path, line in rows:
            if type_name not in seen and len(seen) <= MAX_FAMILY_TYPES:
                seen.add(type_name)
                subs.append((type_name, path, line))
        frontier = {r[0] for r in rows}
    return supers, subs


def collect_change(store: Store, target: str) -> ChangeReport | None:
    parts = [p for p in target.split(".") if p]
    if not parts:
        return None
    if len(parts) >= 2:
        owner, member = parts[-2], parts[-1]
    else:
        name = parts[0]
        if any(r.kind in TYPE_KINDS for r in store.symbols_named(name)):
            owner, member = name, None
        else:
            owner, member = None, name
    rep = ChangeReport(target, owner, member)

    if member is None:  # the target is a type: its mentions are its callers
        rep.defs = [r for r in store.symbols_named(owner) if r.kind in TYPE_KINDS]
        _, rep.subtypes = _family(store, owner)
        def_paths = {r.path for r in rep.defs}
        rep.callers = [RefSite(p, c, lines) for p, c, lines in store.reference_sites(owner)
                       if p not in def_paths or lines]
        return rep if rep.defs or rep.callers else None

    all_defs = store.symbols_named(member)
    if owner is None:  # unqualified member: infer the owner when it is unique
        owners = sorted({o for r in all_defs
                         for o in _owners_of(store, r)})
        if len(owners) == 1:
            owner = rep.owner = owners[0]
        elif owners:
            rep.ambiguous_owners = owners
    if owner:
        rep.defs = [r for path in _type_paths(store, owner)
                    for r in _owned(store, path, member, owner)]
    if not rep.defs:
        rep.defs = all_defs
    rep.extension = any("(this " in r.signature for r in rep.defs)

    family = {owner} if owner else set()
    if owner:
        supers, rep.subtypes = _family(store, owner)
        family |= supers | {t for t, _, _ in rep.subtypes}
        for sup in sorted(supers):
            for path in _type_paths(store, sup):
                rep.declared_by += [Impl(sup, r) for r in _owned(store, path, member, sup)]
        for sub, path, _ in rep.subtypes:
            rep.impls += [Impl(sub, r) for r in _owned(store, path, member, sub)]

    sites = store.reference_sites(member)
    gated = bool(family) and not rep.extension
    family_lines: dict[str, set[int]] = {}
    if gated:
        for name in family:
            for path, _, lines in store.reference_sites(name):
                family_lines.setdefault(path, set()).update(lines or [0])
        for row in rep.defs + [i.row for i in rep.impls + rep.declared_by]:
            family_lines.setdefault(row.path, set()).add(0)
    strong, weak = [], []
    for path, count, lines in sites:
        if is_data_path(path):
            # A config breaks when the member is renamed even if it never
            # names the type (`"$merge": "Union"`): list it, say which.
            named = bool(family_lines.get(path)) if gated else True
            rep.data.append(RefSite(path, count, lines, named))
            continue
        if not gated:
            (strong if not rep.ambiguous_owners else weak).append(
                RefSite(path, count, lines, not rep.ambiguous_owners))
            continue
        if path not in family_lines:
            rep.name_only += 1
            continue
        shared = sorted(set(lines) & family_lines[path])
        if shared:
            strong.append(RefSite(path, count, shared, True))
        else:
            weak.append(RefSite(path, count, lines, False))
    order = lambda s: (-len(s.lines), s.path)  # noqa: E731
    rep.callers = sorted(strong, key=order) + sorted(weak, key=order)
    rep.data.sort(key=lambda s: (not s.same_line, s.path))
    return rep if rep.defs or rep.callers or rep.impls or rep.name_only or rep.data else None


def _owners_of(store: Store, row: SymbolRow) -> set[str]:
    rows = store.symbols_in_file(row.path)
    owners = enclosing_types([(r.name, r.kind, r.line, r.end_line) for r in rows],
                             ("method", "field", "event", "function"))
    return owners.get(row.name, set())


def _blind_callers(store: Store, rep: ChangeReport) -> dict[str, str]:
    """Unity: caller files whose assembly cannot see the definition's
    assembly — a same-name symbol of another type, or code that does not
    compile. path -> suffix."""
    project = unity.load(store)
    home = project.assembly_for(rep.defs[0].path) if project and rep.defs else None
    if home is None:
        return {}
    out = {}
    for site in rep.callers:
        user = project.assembly_for(site.path)
        if user and project.can_see(user[0], home[0]) is False:
            out[site.path] = f"  [{user[0].name} cannot see {home[0].name}]"
    return out


def render_change(store: Store, target: str, limit: int = CALLERS_SHOWN) -> str:
    rep = collect_change(store, target)
    if rep is None:
        return f"(`{target}` not found in index)"
    lines = [f"change {target} — name-based, no type resolution"]
    if rep.ambiguous_owners:
        lines.append(f"note: {len(rep.ambiguous_owners)} types define `{rep.member}` "
                     f"({', '.join(rep.ambiguous_owners[:5])}) — qualify as Type.{rep.member}")
    lines.append("definition:")
    lines += [f"  {r.path}:{r.line}  {r.signature}" for r in rep.defs[:8]]
    if rep.extension:
        lines.append("  (extension method: callers never name its class — all uses listed)")
    if rep.declared_by:
        lines.append("declared by supertypes (change together):")
        lines += [f"  {i.row.path}:{i.row.line}  [{i.type_name}] {i.row.signature}"
                  for i in rep.declared_by[:IMPLS_SHOWN]]
    if rep.member is None and rep.subtypes:
        lines.append(f"subtypes ({len(rep.subtypes)}):")
        lines += [f"  {path}:{line}  {t}" for t, path, line in rep.subtypes[:IMPLS_SHOWN]]
    if rep.impls:
        lines.append(f"overrides / implementations ({len(rep.impls)}):")
        lines += [f"  {i.row.path}:{i.row.line}  [{i.type_name}] {i.row.signature}"
                  for i in rep.impls[:IMPLS_SHOWN]]
        if len(rep.impls) > IMPLS_SHOWN:
            lines.append(f"  … {len(rep.impls) - IMPLS_SHOWN} more")
    if rep.callers:
        tests = sum(1 for s in rep.callers if is_test_path(s.path))
        total_lines = sum(len(s.lines) or 1 for s in rep.callers)
        where = ("" if rep.extension or not rep.owner else
                 " in files naming the type family, type on the line first")
        lines.append(f"callers — {len(rep.callers)} files ({tests} tests), "
                     f"{total_lines} lines{where}:")
        blind = _blind_callers(store, rep)
        for site in rep.callers[:limit]:
            lines.append(_site_line(site) + ("  [test]" if is_test_path(site.path) else "")
                         + blind.get(site.path, ""))
        if len(rep.callers) > limit:
            lines.append(f"  … {len(rep.callers) - limit} more files (raise the limit)")
    else:
        lines.append("callers: none found")
    if rep.data:
        named = sum(1 for s in rep.data if s.same_line)
        lines.append(f"data — {len(rep.data)} config files ({named} name the type):")
        for site in rep.data[:limit]:
            lines.append(_site_line(site) + ("" if site.same_line else "  (name only)"))
    if rep.name_only:
        lines.append(f"name-only: {rep.name_only} more files use `{rep.member}` on types "
                     f"outside this family (not listed; `refs {rep.member}` lists them)")
    lines.append(f"not covered: {NOT_COVERED}")
    return "\n".join(lines)
