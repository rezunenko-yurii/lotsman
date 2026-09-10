"""`lotsman why A B`: the shortest reference path from A to B.

A and B are files, folders, types, `Type.Member`, asmdef or folder names.
One hop = the file names something defined in the next file:
- a type (or its base type in a base list);
- a member, when the file also names the member's type — `Label.text` in
  a UI file is not `MyLabel.text`; free functions and C# extension methods
  need no type, but a file's own function of that name wins, a C#
  extension must be called on an instance (`x.Abs(`, not `Math.Abs(`) and
  a Python function's module must be imported;
- Unity assets: a component's script (`m_Script`), a nested prefab or
  prefab instance, a UnityEvent call.
Unity: a C# hop into an assembly the file's assembly cannot compile against
is a same-named foreign type and is dropped (asmdef references, A1).

Search order, first hit wins: A -> B by specific names; A -> B also through
an interface or base type to its implementations (runtime dispatch, not a
reference; never into test doubles, never right after a base-list hop);
B -> A (the dependency runs the other way); A -> B also through generic
names (`Window`, `Update`) or names defined in many files — likely clashes,
said so. Among the shortest routes the one with the strongest weakest hop
is shown. Name-based like `refs`: DI, reflection, strings and events wired
at runtime are invisible.
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

from lotsman.store import TYPE_KINDS, Store, SymbolRow
from lotsman.textutil import is_generic_name, is_test_path

MAX_HOPS = 8
MAX_DEFINERS = 3        # a name defined in more files is ambiguous (weak)
NAMES_SHOWN = 3
LINES_SHOWN = 3
MEMBER_KINDS = ("method", "field", "event", "function")
_ACCESS_RE = re.compile(r"\b(?:public|internal|protected)\b")
_PY_IMPORT_RE = re.compile(r"\s*(?:from\s+\S+\s+import|import)\s")
_BUILTIN_TYPES = frozenset("string int uint long ulong short ushort byte sbyte char bool "
                           "float double decimal object".split())

# Hop kinds
NAMES = "names"         # code names a type or member of the next file
BASE = "base"           # base list entry
SCRIPT = "script"       # asset component script (m_Script)
PREFAB = "prefab"       # nested prefab / prefab instance / variant
CALL = "call"           # UnityEvent persistent call
IMPL = "impl"           # implemented by (runtime dispatch)

# Search phases
STRONG, WEAK, DISPATCH = 0, 1, 2
PHASE_NOTE = {
    WEAK: "only through generic or many-times-defined names — likely a name clash",
    DISPATCH: "only through an interface or base type to its implementation "
              "(runtime dispatch, not a reference)",
}
_SCORE = {NAMES: 2.0, BASE: 3.0, SCRIPT: 3.0, PREFAB: 3.0, CALL: 3.0, IMPL: 0.5}


@dataclass
class Hop:
    src: str
    dst: str
    kind: str
    names: dict[str, list[int]] = field(default_factory=dict)  # name -> src lines
    types: set[str] = field(default_factory=set)               # names that are types
    weak: bool = False
    calls: set[str] = field(default_factory=set)               # UnityEvent-called names

    def only(self, names: dict[str, list[int]]) -> Hop:
        return Hop(self.src, self.dst, self.kind, names, self.types & set(names),
                   self.weak, self.calls & set(names))

    @property
    def score(self) -> float:
        base = _SCORE[self.kind] + (0.5 if self.types else 0.0)
        return base * (0.25 if self.weak else 1.0)


@dataclass
class Endpoint:
    arg: str
    files: set[str]
    label: str
    names: set[str] | None = None       # target: names the last hop must use
    spans: dict[str, list[tuple[int, int]]] | None = None  # source: lines to leave from
    error: str = ""


@dataclass
class WhyResult:
    a: Endpoint
    b: Endpoint
    hops: list[Hop]
    routes: int = 0
    phase: int = STRONG
    reverse: bool = False  # hops go from B to A
    error: str = ""
    bases: dict[str, set[str]] = field(default_factory=dict)  # path -> base names


class _Defs:
    """Name -> defining files, from the whole index (loaded once per Graph).

    C# scoping, approximated by names: a member counts only where its type
    is named, a nested type or its members only where every enclosing type
    is named too (`Codes.Unparsable` in two files is two different things),
    and private nested types are invisible outside their file."""

    def __init__(self, store: Store):
        self.types: dict[str, set[str]] = defaultdict(set)
        self.type_gates: dict[tuple[str, str], frozenset] = {}  # nested: outer types
        self.hidden: set[tuple[str, str]] = set()               # private nested
        self.members: dict[str, list[tuple[str, frozenset | None]]] = defaultdict(list)
        self.rows: dict[str, list[SymbolRow]] = defaultdict(list)
        for r in store.code_symbols():
            self.rows[r.path].append(r)
        for path, rows in self.rows.items():
            cs = path.endswith(".cs")
            type_rows = [r for r in rows if r.kind in TYPE_KINDS]
            types_here = {r.name for r in type_rows}
            for r in rows:
                chain = [t for t in type_rows if t is not r and t.line <= r.line
                         and r.end_line <= t.end_line]
                private = cs and any(_private_nested(t, type_rows) for t in chain)
                if r.kind in TYPE_KINDS:
                    self.types[r.name].add(path)
                    if chain:
                        self.type_gates[(r.name, path)] = frozenset(t.name for t in chain)
                    if private or (cs and _private_nested(r, type_rows)):
                        self.hidden.add((r.name, path))
                elif r.kind in MEMBER_KINDS and r.name not in types_here and not private:
                    gate = None  # extension methods and free functions: ungated
                    if chain and "(this " not in r.signature:
                        gate = frozenset(t.name for t in chain)
                    self.members[r.name].append((path, gate))
        self.file_bases: dict[str, set[str]] = defaultdict(set)  # path -> base names
        self.subtypes: dict[str, set[tuple[str, str]]] = defaultdict(set)
        for path, type_name, base, _ in store.bases_by_file():
            self.file_bases[path].add(base)
            self.subtypes[base].add((type_name, path))


def _private_nested(r: SymbolRow, type_rows: list[SymbolRow]) -> bool:
    """A nested C# type without an access modifier (private by default)."""
    nested = any(t is not r and t.line <= r.line and r.end_line <= t.end_line
                 for t in type_rows)
    return nested and not _ACCESS_RE.search(r.signature)


class Graph:
    """File -> file hops, loaded lazily; reusable across queries."""

    def __init__(self, store: Store, root: Path | None = None):
        from lotsman import unity
        self.store = store
        self.root = root
        self._texts: dict[str, list[str]] = {}
        self._py_imports: dict[str, set[str]] = {}
        self.defs = _Defs(store)
        self.project = unity.load(store)
        self._asm: dict[str, object] = {}
        self._sees: dict[tuple[str, str], bool] = {}
        self._code: dict[str, dict[str, Hop]] = {}
        self._assets: dict[str, dict[str, Hop]] = {}
        self._langs: dict[str, str] = {}
        self._guids: dict[str, str] | None = None
        self.tests_ok = False  # dispatch may enter test files (target is a test)

    # Unity assemblies ------------------------------------------------------

    def _visible(self, src: str, dst: str) -> bool:
        if self.project is None or not (src.endswith(".cs") and dst.endswith(".cs")):
            return True
        a, b = self._assembly(src), self._assembly(dst)
        if a is None or b is None:
            return True
        key = (a.name, b.name)
        if key not in self._sees:
            self._sees[key] = self.project.can_see(a, b) is not False
        return self._sees[key]

    def _assembly(self, path: str):
        if path not in self._asm:
            found = self.project.assembly_for(path)
            self._asm[path] = found[0] if found else None
        return self._asm[path]

    # edges -----------------------------------------------------------------

    def out_edges(self, paths: list[str], phase: int) -> dict[str, dict[str, Hop]]:
        todo = [p for p in paths if p not in self._code]
        if todo:
            self._langs.update(self.store.langs_of(todo))
            self._load_code([p for p in todo if self._langs.get(p) not in
                             (None, "markdown", "unityasset", "unitymeta", "asmdef")])
            self._load_assets([p for p in todo if self._langs.get(p) == "unityasset"])
            for p in todo:
                self._code.setdefault(p, {})
        out: dict[str, dict[str, Hop]] = {}
        for p in paths:
            edges = {d: h for d, h in self._code[p].items() if phase == WEAK or not h.weak}
            edges.update(self._assets.get(p, {}))
            if phase >= DISPATCH:
                for d, h in self._dispatch(p).items():
                    edges.setdefault(d, h)
            out[p] = edges
        return out

    def _load_code(self, paths: list[str]) -> None:
        defs = self.defs
        for src, idents in self.store.idents_in(paths).items():
            named = {n for n, _, _ in idents}
            own = {r.name for r in defs.rows.get(src, ()) if r.kind in TYPE_KINDS}
            own_members = {r.name for r in defs.rows.get(src, ()) if r.kind in MEMBER_KINDS}
            json_file = self._langs.get(src) == "json"
            bases = defs.file_bases.get(src, set())
            strong: dict[str, dict[str, list[int]]] = defaultdict(dict)
            weak: dict[str, dict[str, list[int]]] = defaultdict(dict)
            types: dict[str, set[str]] = defaultdict(set)
            for name, _, lines in idents:
                generic = is_generic_name(name)
                owners = defs.types.get(name, ())
                for dst in () if name in own else sorted(owners):  # own types shadow
                    gate = defs.type_gates.get((name, dst))
                    if (name, dst) in defs.hidden or (gate and not gate <= named):
                        continue
                    (weak if generic or len(owners) > MAX_DEFINERS else strong)[dst][name] = lines
                    types[dst].add(name)
                if json_file or generic:
                    continue
                members = defs.members.get(name, ())
                ambiguous = len({d for d, _ in members}) > MAX_DEFINERS
                for dst, gate in members:
                    if gate is None and (name in own_members or (
                            dst.endswith(".cs") and src.endswith(".cs")
                            and not self._instance_call(src, name, lines)) or (
                            dst.endswith(".py") and src.endswith(".py")
                            and not self._imports(src, dst))):
                        continue  # free function / extension: own one, `Math.Abs(`, not imported
                    if gate is None or gate <= named:
                        (weak if ambiguous else strong)[dst][name] = lines
            hops: dict[str, Hop] = {}
            for dst in sorted(set(strong) | set(weak)):
                if dst == src or not self._visible(src, dst):
                    continue
                names = strong.get(dst) or weak[dst]
                kind = BASE if not bases.isdisjoint(names) else NAMES
                hops[dst] = Hop(src, dst, kind, names, types[dst] & set(names),
                                weak=not strong.get(dst))
            self._code[src] = hops

    def _source(self, path: str) -> list[str]:
        if path not in self._texts:
            try:
                self._texts[path] = (self.root / path).read_text(errors="replace").split("\n")
            except OSError:
                self._texts[path] = []
        return self._texts[path]

    def _imports(self, src: str, dst: str) -> bool:
        """Python: `src` imports the module `dst` (its stem, or its package
        for `__init__.py`) — a module function is reached only through that."""
        if self.root is None:
            return True
        if src not in self._py_imports:
            words: set[str] = set()
            for ln in self._source(src):
                if _PY_IMPORT_RE.match(ln):
                    words.update(re.findall(r"\w+", ln))
            self._py_imports[src] = words
        stem = PurePosixPath(dst)
        module = stem.parent.name if stem.name == "__init__.py" else stem.stem
        return module in self._py_imports[src]

    def _instance_call(self, src: str, name: str, lines: list[int]) -> bool:
        """`x.Name(` / `Get().Name(` somewhere on the lines — an extension
        method call; `Math.Abs(` or `string.IsNullOrEmpty(` is a static
        call of another type's method."""
        if self.root is None:
            return True
        text = self._source(src)
        call = re.compile(r"([\w)\]]+)\s*\??\.\s*" + re.escape(name) + r"\s*[(<]")
        for n in lines:
            for m in call.finditer(text[n - 1] if 0 < n <= len(text) else ""):
                receiver = m.group(1)
                if receiver[-1] in ")]" or receiver == "this" or (
                        receiver[0].islower() and receiver not in _BUILTIN_TYPES) \
                        or receiver[0] == "_":
                    return True
        return False

    def _load_assets(self, paths: list[str]) -> None:
        from lotsman import unityassets as ua
        if not paths:
            return
        if self._guids is None:
            self._guids = {g: p for p, g in self.store.meta_guids().items()}
        for src, kind, key, owner, line in self.store.asset_refs_of(
                paths, (ua.SCRIPT, ua.PREFAB, ua.CALL)):
            if kind == ua.CALL:
                if owner.startswith("S:"):
                    dsts = [self._guids.get(owner[2:])]
                elif owner.startswith("T:"):
                    dsts = sorted(self.defs.types.get(owner[2:], ()))
                else:
                    continue
                label, hop_kind = key, CALL
            else:
                dsts = [self._guids.get(key)]
                label = PurePosixPath(dsts[0]).stem if dsts[0] else ""
                hop_kind = SCRIPT if kind == ua.SCRIPT else PREFAB
            for dst in dsts:
                if not dst or dst == src:
                    continue
                hops = self._assets.setdefault(src, {})
                hop = hops.get(dst) or hops.setdefault(dst, Hop(src, dst, hop_kind))
                if hop.kind == CALL and hop_kind != CALL:
                    hop.kind = hop_kind  # the script hop carries the calls too
                hop.names.setdefault(label, []).append(line)
                if hop_kind == CALL:
                    hop.calls.add(label)

    def _dispatch(self, path: str) -> dict[str, Hop]:
        """Interface / base type defined here -> files implementing it (test
        doubles excluded: they implement everything and run nowhere)."""
        out: dict[str, Hop] = {}
        for r in self.defs.rows.get(path, ()):
            if r.kind not in TYPE_KINDS:
                continue
            for sub, dst in sorted(self.defs.subtypes.get(r.name, ())):
                if dst != path and self._visible(dst, path) and (
                        self.tests_ok or not is_test_path(dst)):
                    hop = out.get(dst) or out.setdefault(dst, Hop(path, dst, IMPL))
                    hop.names.setdefault(sub, [])
                    hop.types.add(sub)
        return out


# --- endpoints ---------------------------------------------------------------

def resolve(arg: str, graph: Graph, known: set[str]) -> Endpoint:
    """File, folder, type, `Type.Member`, asmdef or folder name -> Endpoint
    (files empty and `error` set when nothing matches)."""
    rel = arg.strip().removeprefix("./").rstrip("/")
    end = _path_endpoint(arg, rel, known) or _symbol_endpoint(arg, rel, graph.defs)
    if end is None and graph.project is not None and rel in graph.project.by_name:
        files = {p for p in known if p.endswith(".cs")
                 and (found := graph.project.assembly_for(p)) and found[0].name == rel}
        if files:
            end = Endpoint(arg, files, f"{rel} (assembly)")
    if end is None and "/" not in rel:
        dirs = sorted({q.rpartition("/" + rel + "/")[0] + "/" + rel for q in known
                       if ("/" + q).find("/" + rel + "/") >= 0})
        if len(dirs) == 1:
            d = dirs[0].lstrip("/")
            end = Endpoint(arg, {q for q in known if q.startswith(d + "/")}, d + "/")
        elif dirs:
            shown = ", ".join(d.lstrip("/") + "/" for d in dirs[:3])
            return Endpoint(arg, set(), arg, error=f"{len(dirs)} folders are named `{rel}` "
                                                   f"({shown}…): pass a path")
    return end or Endpoint(arg, set(), arg, error=f"`{arg}` is not a file, folder, "
                                                  "assembly or symbol in the index")


def _path_endpoint(arg: str, rel: str, known: set[str]) -> Endpoint | None:
    if rel in known:
        return Endpoint(arg, {rel}, rel)
    if "/" in rel or PurePosixPath(rel).suffix in (".cs", ".prefab", ".unity", ".asset",
                                                    ".json", ".py", ".ts", ".go"):
        under = {p for p in known if p.startswith(rel + "/")}
        if under:
            return Endpoint(arg, under, rel + "/")
        tail = {p for p in known if p.endswith("/" + rel) or rel.endswith("/" + p)}
        if tail:
            return Endpoint(arg, tail, sorted(tail)[0] if len(tail) == 1 else arg)
    return None


def _symbol_endpoint(arg: str, rel: str, defs: _Defs) -> Endpoint | None:
    parts = [p for p in rel.split(".") if p]
    if not parts:
        return None
    name = parts[-1]
    if name in defs.types and len(parts) == 1:
        files = defs.types[name]
        spans: dict[str, list[tuple[int, int]]] = defaultdict(list)
        names = {name}
        for path in files:
            for r in defs.rows[path]:
                if r.kind in TYPE_KINDS and r.name == name:
                    spans[path].append((r.line, r.end_line))
            for r in defs.rows[path]:
                if r.kind not in TYPE_KINDS and any(s <= r.line <= e for s, e in spans[path]):
                    names.add(r.name)
        return Endpoint(arg, set(files), name, names, dict(spans))
    rows = [r for path, rs in defs.rows.items() for r in rs if r.name == name
            and r.kind not in TYPE_KINDS]
    if len(parts) >= 2:
        owner = parts[-2]
        rows = [r for r in rows if owner in defs.types and r.path in defs.types[owner]] or rows
    if not rows:
        return None
    spans = defaultdict(list)
    for r in rows:
        spans[r.path].append((r.line, r.end_line))
    return Endpoint(arg, {r.path for r in rows}, ".".join(parts[-2:]), {name}, dict(spans))


# --- search ------------------------------------------------------------------

def _in_spans(lines: list[int], spans: list[tuple[int, int]]) -> list[int]:
    return [n for n in lines if any(s <= n <= e for s, e in spans)]


_SRC, _DST = "\0a", "\0b"
_VIA_BASE = "\0^"  # node suffix: reached through a base list (no dispatch out)


def _search(graph: Graph, a: Endpoint, b: Endpoint, phase: int
            ) -> tuple[list[Hop], int] | None:
    """Layered BFS from a's files; (best shortest route, number of routes).
    Dispatch never follows a base-list hop: `A : Base` does not make A
    depend on Base's other subclasses."""
    dist = {_SRC: 0, **{f: 0 for f in a.files}}
    preds: dict[str, list[tuple[str, Hop]]] = defaultdict(list)
    frontier = [_SRC]
    for depth in range(1, MAX_HOPS + 1):
        files = {u.removesuffix(_VIA_BASE) for u in frontier if u != _SRC}
        if _SRC in frontier:
            files |= a.files
        loaded = graph.out_edges(sorted(files), phase)
        nxt: set[str] = set()
        for u in frontier:
            if u == _SRC:
                out = [_restrict_src(h, a) for f in sorted(a.files)
                       for d, h in loaded[f].items() if d not in a.files]
            else:
                f = u.removesuffix(_VIA_BASE)
                out = [h for h in loaded[f].values()
                       if not (u.endswith(_VIA_BASE) and h.kind == IMPL)]
            for hop in out:
                if hop is None:
                    continue
                v = hop.dst + (_VIA_BASE if phase >= DISPATCH and hop.kind == BASE else "")
                steps = [(v, hop)]
                if hop.dst in b.files:
                    last = _restrict_dst(hop, b)
                    if last is not None:
                        steps.append((_DST, last))
                for t, h in steps:
                    if t in dist and dist[t] < depth:
                        continue
                    if t not in dist:
                        dist[t] = depth
                        nxt.add(t)
                    preds[t].append((u, h))
        if _DST in nxt:
            return _best(preds, dist)
        frontier = sorted(nxt)
        if not frontier:
            return None
    return None


def _restrict_src(hop: Hop, a: Endpoint) -> Hop | None:
    """A type or member as the source: only names used inside it."""
    if not a.spans or hop.src not in a.spans:
        return hop
    names = {}
    for n, lines in hop.names.items():
        inside = _in_spans(lines, a.spans[hop.src])
        if inside or not lines:
            names[n] = inside
    if not names:
        return None
    return hop.only(names)


def _restrict_dst(hop: Hop, b: Endpoint) -> Hop | None:
    """A type or member as the target: the last hop must name it."""
    if b.names is None:
        return hop
    names = {n: ls for n, ls in hop.names.items() if n in b.names}
    if not names:
        return None
    return hop.only(names)


def _best(preds: dict[str, list[tuple[str, Hop]]], dist: dict[str, int]
          ) -> tuple[list[Hop], int]:
    """Strongest route (max weakest hop, then total) over the shortest-path DAG."""
    memo: dict[str, tuple[float, float, list[Hop], int]] = {_SRC: (float("inf"), 0.0, [], 1)}
    for n in sorted((n for n in dist if n in preds), key=lambda n: dist[n]):
        best, routes = None, 0
        for u, hop in preds[n]:
            if u not in memo or dist[u] != dist[n] - 1:
                continue
            lo, total, path, count = memo[u]
            routes += count
            cand = (min(lo, hop.score), total + hop.score, path + [hop])
            if best is None or cand[:2] > best[:2]:
                best = cand
        if best is not None:
            memo[n] = (*best, min(routes, 10**6))
    _, _, path, routes = memo[_DST]
    return path, routes


def find_path(store: Store, a_arg: str, b_arg: str, root: Path | None = None,
              graph: Graph | None = None) -> WhyResult:
    """Shortest reference route from A to B (see module doc); `root` lets
    extension-method and Python-import hops be checked against the source."""
    graph = graph or Graph(store, root)
    known = {p for p in store.known_files() if not p.endswith(".meta")}
    a = resolve(a_arg, graph, known)
    b = resolve(b_arg, graph, known)
    for end in (a, b):
        if not end.files:
            return WhyResult(a, b, [], error=end.error)
    if a.files & b.files:
        both = ", ".join(sorted(a.files & b.files)[:3])
        return WhyResult(a, b, [], error=f"both are in {both} (outline it)")
    graph.tests_ok = any(is_test_path(p) for p in b.files)
    for phase, reverse in ((STRONG, False), (DISPATCH, False), (STRONG, True), (WEAK, False)):
        found = _search(graph, b, a, phase) if reverse else _search(graph, a, b, phase)
        if found:
            bases = {h.src: graph.defs.file_bases.get(h.src, set()) for h in found[0]}
            return WhyResult(a, b, found[0], found[1], phase, reverse, bases=bases)
    return WhyResult(a, b, [])


# --- rendering -----------------------------------------------------------------

def _names_text(hop: Hop, items: list[tuple[str, list[int]]]) -> str:
    shown = []
    for name, lines in items[:NAMES_SHOWN]:
        at = ",".join(map(str, sorted(set(lines))[:LINES_SHOWN]))
        more = "+" if len(set(lines)) > LINES_SHOWN else ""
        shown.append(f"{name} :{at}{more}" if at else name)
    rest = f" (+{len(items) - NAMES_SHOWN} names)" if len(items) > NAMES_SHOWN else ""
    return ", ".join(shown) + rest


def _ordered(hop: Hop) -> list[tuple[str, list[int]]]:
    """Types first, then the most-used names."""
    return sorted(hop.names.items(),
                  key=lambda kv: (kv[0] not in hop.types, -len(set(kv[1])), kv[0]))


def _hop_line(hop: Hop, bases: set[str]) -> str:
    items = _ordered(hop)
    verb = {NAMES: "names", SCRIPT: "uses script", PREFAB: "contains prefab",
            CALL: "UnityEvent calls", IMPL: "implemented by (dispatch)"}
    calls = [kv for kv in items if kv[0] in hop.calls]
    items = [kv for kv in items if kv[0] not in hop.calls]
    parts = []
    if hop.kind == BASE:
        derives = [kv for kv in items if kv[0] in bases]
        others = [kv for kv in items if kv[0] not in bases]
        parts.append("derives from " + _names_text(hop, derives))
        if others:
            parts.append("names " + _names_text(hop, others))
    elif items:
        parts.append(f"{verb[hop.kind]} {_names_text(hop, items)}")
    if calls:
        parts.append("UnityEvent calls " + _names_text(hop, calls))
    text = "; ".join(parts)
    weak = "  [generic/ambiguous name]" if hop.weak else ""
    return f"    ↳ {text}{weak}"


def render_why(res: WhyResult) -> str:
    a, b = res.a, res.b
    if res.error:
        return f"why {a.arg} → {b.arg}: {res.error}"
    if not res.hops:
        return (f"no reference path between {a.label} and {b.label} in either direction "
                "(name-based: links made at runtime — DI, reflection, strings, events "
                "wired in code — are invisible)")
    src, dst = (b, a) if res.reverse else (a, b)
    n = len(res.hops)
    lines = []
    if res.reverse:
        lines.append(f"{a.label} does not reach {b.label} by name; the other way round:")
    routes = (f"; {res.routes} routes of this length, the strongest shown"
              if res.routes > 1 else "")
    lines.append(f"{src.label} → {dst.label}: {n} hop{'s' if n > 1 else ''} "
                 f"(name-based, shortest{routes})")
    if res.phase in PHASE_NOTE:
        lines.append(f"note: {PHASE_NOTE[res.phase]}")
    for hop in res.hops:
        lines.append(f"  {hop.src}")
        lines.append(_hop_line(hop, res.bases.get(hop.src, set())))
    lines.append(f"  {res.hops[-1].dst}")
    return "\n".join(lines)


def why_json(res: WhyResult) -> dict:
    return {
        "from": res.a.arg, "to": res.b.arg,
        "error": res.error or None,
        "direction": ("none" if not res.hops else "reverse" if res.reverse else "forward"),
        "via": {STRONG: "names", WEAK: "weak names", DISPATCH: "dispatch"}[res.phase]
        if res.hops else None,
        "routes": res.routes,
        "hops": [{"from": h.src, "to": h.dst, "kind": h.kind, "weak": h.weak,
                  "unity_event_calls": sorted(h.calls),
                  "names": [{"name": n, "lines": sorted(set(ls))} for n, ls in _ordered(h)]}
                 for h in res.hops],
    }
