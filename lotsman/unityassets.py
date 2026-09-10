"""Serialized Unity assets as references (prefabs, scenes, ScriptableObjects,
animations).

Assets reference code in four ways, all in text-mode YAML:

- a component or ScriptableObject names its script by GUID
  (`m_Script: {fileID: 11500000, guid: G}`); the GUID lives in the script's
  `.meta`, so the link is resolved at query time and survives renames;
- UnityEvent persistent calls name a method (`m_MethodName`) and its type
  (`m_TargetAssemblyTypeName`, 2020.2+) or only the target object — a
  component in the same file (resolved here through its `m_Script`) or
  another asset (resolved at query time);
- animation events name a method (`functionName`), receiver unknown;
- a component's serialized fields are its YAML keys (a rename loses the
  value unless `[FormerlySerializedAs]`).

Prefab instances and variants name their source prefab by GUID
(`m_SourcePrefab`), which lets a script's users be followed through nested
prefabs into scenes. Binary assets and `stripped` documents are skipped.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

ASSET_LANG = "unityasset"
ASSET_SUFFIXES = (".prefab", ".unity", ".asset", ".anim", ".controller",
                  ".overrideController", ".playable", ".signal", ".mixer")
META_LANG = "unitymeta"
MAX_ASSET_BYTES = 50_000_000  # scenes grow large; parsing is one regex pass
MODEL_SUFFIXES = (".fbx", ".obj", ".blend", ".dae", ".max", ".ma", ".mb", ".3ds")
_ASSET_LOWER = tuple(s.lower() for s in ASSET_SUFFIXES)

SCRIPT_CLASS = 114       # MonoBehaviour / ScriptableObject
ANIMATION_CLIP = 74
PREFAB_INSTANCE = 1001

# Keys every MonoBehaviour document has; everything else at the top level is
# a serialized field of the script.
BUILTIN_KEYS = frozenset({
    "m_ObjectHideFlags", "m_CorrespondingSourceObject", "m_PrefabInstance",
    "m_PrefabAsset", "m_GameObject", "m_Enabled", "m_EditorHideFlags", "m_Script",
    "m_Name", "m_EditorClassIdentifier", "m_PrefabInternal", "m_PrefabParentObject",
})

_DOC_RE = re.compile(rb"^--- !u!(\d+) &(-?\d+)( stripped)?", re.M)
_SCRIPT_RE = re.compile(rb"^  m_Script: \{fileID: 11500000, guid: ([0-9a-f]{32})", re.M)
_KEY_RE = re.compile(rb"^  (\w+):", re.M)
_CALL_RE = re.compile(
    rb"- m_Target: \{fileID: (-?\d+)(?:, guid: ([0-9a-f]{32}))?[^}\n]*\}\s*\n"
    rb"(?:[ \t]*m_TargetAssemblyTypeName:[ \t]*([^\n]*)\n)?"
    rb"[ \t]*m_MethodName:[ \t]*(\w*)")
_ANIM_EVENT_RE = re.compile(rb"functionName: (\w+)")
_SOURCE_PREFAB_RE = re.compile(
    rb"m_(?:SourcePrefab|ParentPrefab): \{fileID: -?\d+, guid: ([0-9a-f]{32})")
_META_GUID_RE = re.compile(rb"^guid: ([0-9a-f]{32})", re.M)

# Row kinds (asset_refs.kind)
SCRIPT = "script"   # key: script GUID
CALL = "call"       # key: method; owner: "T:Type" | "S:script guid" | "A:asset guid"
                    #   | "X:Type" (target object gone) | "" (GameObject target)
ANIM = "anim"       # key: method (animation event)
FIELD = "field"     # key: field; owner: "S:script guid"
PREFAB = "prefab"   # key: source prefab GUID (nested prefab / variant / instance)


@dataclass(frozen=True)
class AssetRef:
    kind: str
    key: str
    owner: str
    line: int
    count: int = 1


def lang_for(path: str) -> str | None:
    """Scanner language of a Unity-only file: a serialized asset, or the
    `.meta` of a script, asset or model (GUIDs; model import settings carry
    animation events). Other `.meta` files say nothing about code."""
    low = path.lower()
    if low.endswith(_ASSET_LOWER):
        return ASSET_LANG
    if low.endswith(".meta") and low[:-5].endswith((".cs", *_ASSET_LOWER, *MODEL_SUFFIXES)):
        return META_LANG
    return None


def is_model_meta(path: str) -> bool:
    return path.lower()[:-5].endswith(MODEL_SUFFIXES)


def is_text_asset(data: bytes) -> bool:
    return data.startswith(b"%YAML")


def meta_guid(data: bytes | None) -> str | None:
    if not data:
        return None
    m = _META_GUID_RE.search(data)
    return m.group(1).decode() if m else None


def _method_key(name: str) -> str:
    # UnityEvents call property setters as `set_Name`.
    return name[4:] if name.startswith(("set_", "get_")) and len(name) > 4 else name


def parse_asset(data: bytes) -> list[AssetRef]:
    """Reference facts of one text asset, deduplicated per (kind, key, owner)
    with the first line and a count."""
    if not is_text_asset(data):
        return []
    docs = [(m.start(), int(m.group(1)), m.group(2), bool(m.group(3)))
            for m in _DOC_RE.finditer(data)]
    found: dict[tuple[str, str, str], list[int]] = {}   # -> [line, count]

    def add(kind: str, key: str, owner: str, line: int) -> None:
        slot = found.get((kind, key, owner))
        if slot is None:
            found[(kind, key, owner)] = [line, 1]
        else:
            slot[1] += 1

    local_script: dict[bytes, str] = {}   # document fileID -> script GUID
    pending_calls: list[tuple[bytes, bytes | None, str, str, int]] = []
    line = 1
    prev = 0
    for i, (start, cls, fid, stripped) in enumerate(docs):
        line += data.count(b"\n", prev, start)
        prev = start
        end = docs[i + 1][0] if i + 1 < len(docs) else len(data)
        if cls == SCRIPT_CLASS:
            doc = data[start:end]
            sm = _SCRIPT_RE.search(doc)
            if not sm:
                continue
            guid = sm.group(1).decode()
            local_script[fid] = guid
            # A `stripped` document is a prefab instance's component that
            # something in this file points at: a use, without fields.
            add(SCRIPT, guid, "", line + doc.count(b"\n", 0, sm.start()))
            if stripped:
                continue
            for km in _KEY_RE.finditer(doc, sm.end()):
                key = km.group(1).decode()
                if key not in BUILTIN_KEYS:
                    add(FIELD, key, "S:" + guid, line + doc.count(b"\n", 0, km.start()))
            if b"m_PersistentCalls" in doc:
                for cm in _CALL_RE.finditer(doc):
                    method = cm.group(4).decode()
                    if method:
                        type_name = (cm.group(3) or b"").decode().split(",")[0].strip()
                        pending_calls.append((cm.group(1), cm.group(2), type_name, method,
                                              line + doc.count(b"\n", 0, cm.start(4))))
        elif cls == ANIMATION_CLIP:
            doc = data[start:end]
            for am in _ANIM_EVENT_RE.finditer(doc):
                add(ANIM, am.group(1).decode(), "", line + doc.count(b"\n", 0, am.start()))
        elif cls == PREFAB_INSTANCE:
            doc = data[start:end]
            pm = _SOURCE_PREFAB_RE.search(doc)
            if pm:
                add(PREFAB, pm.group(1).decode(), "", line + doc.count(b"\n", 0, pm.start()))
    for target, target_guid, type_name, method, ln in pending_calls:
        # The target component's script first: the serialized type name goes
        # stale when the class is renamed (the GUID link does not).
        short = type_name.replace("+", ".").rsplit(".", 1)[-1] if type_name else ""
        if target_guid:
            owner = "A:" + target_guid.decode()
        elif target == b"0":
            owner = "X:" + short  # the target object is gone: the call does nothing
        elif target in local_script:
            owner = "S:" + local_script[target]
        elif short:
            owner = "T:" + short
        else:
            owner = ""  # a GameObject target: any of its components
        add(CALL, _method_key(method), owner, ln)
    return [AssetRef(k, key, owner, ln, n) for (k, key, owner), (ln, n) in found.items()]


def parse_model_meta(data: bytes) -> list[AssetRef]:
    """Animation events defined in a model's import settings (`.fbx.meta`)."""
    out: dict[str, list[int]] = {}
    for m in _ANIM_EVENT_RE.finditer(data):
        slot = out.setdefault(m.group(1).decode(), [data.count(b"\n", 0, m.start()) + 1, 0])
        slot[1] += 1
    return [AssetRef(ANIM, k, "", ln, n) for k, (ln, n) in out.items()]


# --- queries (refs / change / impact) ---------------------------------------

FILES_SHOWN = 10
MAX_NESTED_DEPTH = 6
MAX_NESTED = 2000


@dataclass
class AssetSite:
    path: str
    line: int
    count: int
    note: str = ""       # resolved owner, or the prefab a nested user goes through


@dataclass
class AssetUses:
    scripts: list[AssetSite]      # assets with a component / SO of the script
    nested: list[AssetSite]       # assets reaching those through prefab instances
    calls: list[AssetSite]        # UnityEvent persistent calls
    anims: list[AssetSite]        # animation events (receiver unknown)
    fields: list[AssetSite]       # components serializing the field

    def __bool__(self) -> bool:
        return bool(self.scripts or self.calls or self.anims or self.fields)

    def files(self) -> set[str]:
        return {s.path for s in self.scripts + self.nested + self.calls + self.anims + self.fields}


def script_paths(defs) -> list[str]:
    """C# files that can be a script for these type definitions: Unity binds
    a MonoBehaviour/ScriptableObject class to the file of the same name."""
    from lotsman.store import TYPE_KINDS
    return sorted({r.path for r in defs if r.kind in TYPE_KINDS and r.path.endswith(".cs")
                   and r.path.rsplit("/", 1)[-1][:-3] == r.name})


def _stem(path: str) -> str:
    return path.rsplit("/", 1)[-1].rsplit(".", 1)[0]


def script_users(store, paths: list[str]) -> tuple[list[AssetSite], list[AssetSite]]:
    """(direct users, users through nested prefabs / prefab instances)."""
    guids = [g for g in (store.guid_of(p) for p in paths) if g]
    direct = [AssetSite(path, line, count)
              for path, _, _, _, line, count in store.asset_rows((SCRIPT,), guids)]
    seen = {s.path for s in direct}
    frontier = sorted(p for p in seen if p.endswith(".prefab"))
    nested: list[AssetSite] = []
    for _ in range(MAX_NESTED_DEPTH):
        if not frontier or len(nested) >= MAX_NESTED:
            break
        by_guid = {g: p for p in frontier if (g := store.guid_of(p))}
        nxt = []
        for path, _, key, _, line, count in store.asset_rows((PREFAB,), list(by_guid)):
            if path not in seen:
                seen.add(path)
                nested.append(AssetSite(path, line, count, by_guid[key]))
                if path.endswith(".prefab"):
                    nxt.append(path)
        frontier = nxt
    return direct, nested


def _owner_names(store, owners: list[str]) -> dict[str, set[str]]:
    """Resolve call owners ("T:Type", "S:guid", "A:guid") to type names."""
    out: dict[str, set[str]] = {}
    script_guids = [o[2:] for o in owners if o.startswith("S:")]
    asset_guids = [o[2:] for o in owners if o.startswith("A:")]
    paths = store.guid_paths(script_guids + asset_guids)
    asset_scripts: dict[str, set[str]] = {}
    asset_paths = [paths[g] for g in asset_guids if g in paths]
    if asset_paths:
        rows = store.asset_rows_in(asset_paths, SCRIPT)
        sp = store.guid_paths(sorted({key for _, key, _ in rows}))
        for path, key, _ in rows:
            if key in sp:
                asset_scripts.setdefault(path, set()).add(_stem(sp[key]))
    for o in owners:
        if o.startswith(("T:", "X:")):
            out[o] = {o[2:]} if o[2:] else set()
        elif o.startswith("S:"):
            out[o] = {_stem(paths[o[2:]])} if o[2:] in paths else set()
        elif o.startswith("A:"):
            out[o] = asset_scripts.get(paths.get(o[2:], ""), set())
        else:
            out[o] = set()
    return out


def member_uses(store, name: str, owners: set[str] | None) -> tuple[list, list, list]:
    """(UnityEvent calls, animation events, serialized fields) naming `name`.
    With `owners` (the declaring type and its family), calls and fields
    resolved to another type are dropped; unresolved calls stay, marked."""
    rows = store.asset_rows((CALL, ANIM, FIELD), [name])
    resolved = _owner_names(store, sorted({r[3] for r in rows if r[1] in (CALL, FIELD)}))
    calls, anims, fields = [], [], []
    for path, kind, _, owner, line, count in rows:
        if kind == ANIM:
            anims.append(AssetSite(path, line, count))
            continue
        types = resolved.get(owner, set())
        if owners is not None and types and not (types & owners):
            continue
        if kind == FIELD and not types:
            continue  # a field of a script outside the index (package)
        note = "/".join(sorted(types)) or "owner not recorded"
        if owner.startswith("X:"):
            note += " — target object gone"
        site = AssetSite(path, line, count, note)
        (calls if kind == CALL else fields).append(site)
    return calls, anims, fields


def owners_for(store, defs) -> set[str] | None:
    """Declaring types of member definitions plus their families (a
    UnityEvent on a derived component names the derived type); None when
    no definition has an owner."""
    from lotsman.changeview import _family, _owners_of
    owners = {o for r in defs for o in _owners_of(store, r)}
    if not owners:
        return None
    out = set(owners)
    for o in owners:
        supers, subs = _family(store, o)
        out |= supers | {t for t, _, _ in subs}
    return out


def collect_uses(store, member: str, defs, owners: set[str] | None) -> AssetUses:
    """Asset uses of a definition set: script users for types, UnityEvent /
    animation-event calls for methods and properties, serialized keys for
    fields."""
    from lotsman.store import TYPE_KINDS
    scripts, nested = script_users(store, script_paths(defs))
    calls: list = []
    anims: list = []
    fields: list = []
    if any(r.kind not in TYPE_KINDS for r in defs):
        calls, anims, fields = member_uses(store, member, owners)
    return AssetUses(scripts, nested, calls, anims, fields)


def uses_json(uses: AssetUses | None) -> dict:
    if not uses:
        return {}
    site = lambda s: {"path": s.path, "line": s.line, "count": s.count,  # noqa: E731
                      **({"note": s.note} if s.note else {})}
    return {"scripts": [site(s) for s in uses.scripts],
            "nested": [{"path": s.path, "line": s.line, "via": s.note} for s in uses.nested],
            "unity_events": [site(s) for s in uses.calls],
            "animation_events": [site(s) for s in uses.anims],
            "fields": [site(s) for s in uses.fields]}


def _site(s: AssetSite) -> str:
    times = f" (×{s.count})" if s.count > 1 else ""
    note = f"  {s.note}" if s.note else ""
    return f"  {s.path}:{s.line}{times}{note}"


def _listing(sites: list[AssetSite], limit: int) -> list[str]:
    out = [_site(s) for s in sites[:limit]]
    if len(sites) > limit:
        out.append(f"  … {len(sites) - limit} more")
    return out


def render_uses(uses: AssetUses, limit: int = FILES_SHOWN) -> list[str]:
    out: list[str] = []
    if uses.scripts:
        out.append(f"assets — {len({s.path for s in uses.scripts})} files use the script "
                   "(m_Script by GUID):")
        out += _listing(uses.scripts, limit)
    if uses.nested:
        via = ", ".join(f"{s.path} (via {_stem(s.note)})" for s in uses.nested[:3])
        out.append(f"  + {len(uses.nested)} more through prefab instances / variants: {via}"
                   + (" …" if len(uses.nested) > 3 else ""))
    if uses.calls:
        out.append(f"assets — {len(uses.calls)} UnityEvent calls name it (a rename breaks "
                   "them silently, no compile error):")
        out += _listing(uses.calls, limit)
    if uses.anims:
        out.append(f"assets — {len(uses.anims)} animation events name it (receiver not "
                   "recorded; name match):")
        out += _listing(uses.anims, limit)
    if uses.fields:
        out.append(f"assets — {len({s.path for s in uses.fields})} files serialize it as a "
                   "field (a rename drops the value unless [FormerlySerializedAs]):")
        out += _listing(uses.fields, limit)
    return out


def impact_lines(store, changed: list[str]) -> list[str]:
    """`impact`: per changed C# file, the assets that use its script and the
    UnityEvent / animation-event calls and serialized fields naming its
    members — they fail silently, not at compile time."""
    from lotsman import unity
    from lotsman.store import TYPE_KINDS
    if store.get_meta(unity.META_UNITY_ASSETS) is None:
        return []
    out: list[str] = []
    for path in changed:
        if not path.endswith(".cs"):
            continue
        syms = store.symbols_in_file(path)
        parts: list[str] = []
        scripts, nested = script_users(store, script_paths(syms))
        if scripts:
            names = ", ".join(sorted({s.path for s in scripts})[:3])
            more = f" +{len(nested)} via prefab instances" if nested else ""
            parts.append(f"script in {len({s.path for s in scripts})} assets{more} ({names}"
                         + (" …)" if len(scripts) > 3 else ")"))
        types = {s.name for s in syms if s.kind in TYPE_KINDS}
        owners = set(types)
        for t in types:
            from lotsman.changeview import _family
            supers, subs = _family(store, t)
            owners |= supers | {n for n, _, _ in subs}
        called: dict[str, list[AssetSite]] = {}
        fields: set[str] = set()
        for name in sorted({s.name for s in syms if s.kind not in TYPE_KINDS}):
            calls, anims, flds = member_uses(store, name, owners or None)
            if calls or anims:
                called[name] = calls + anims
            fields |= {s.path for s in flds}
        if called:
            shown = [f"{n} ({sites[0].path}:{sites[0].line})" if len(sites) == 1
                     else f"{n} ({len({s.path for s in sites})} assets)"
                     for n, sites in sorted(called.items(), key=lambda x: -len(x[1]))]
            parts.append("called from assets: " + ", ".join(shown[:5])
                         + (" …" if len(shown) > 5 else ""))
        if fields:
            parts.append(f"fields serialized in {len(fields)} assets")
        if parts:
            out.append(f"  {path}: " + "; ".join(parts))
    if out:
        out.insert(0, "\nAssets (Unity; they break silently — GUID-bound scripts, calls by "
                      "method name, fields by key):")
    return out


# --- broken references (`unity missing`) --------------------------------------

# Members UnityEvents often call on a project component that come from
# Unity's own base classes (not in the index): never reported as stale.
UNITY_INHERITED = frozenset({
    "enabled", "SetActive", "name", "tag", "hideFlags", "useGUILayout", "runInEditMode",
    "SendMessage", "SendMessageUpwards", "BroadcastMessage", "Invoke", "InvokeRepeating",
    "CancelInvoke", "StartCoroutine", "StopCoroutine", "StopAllCoroutines",
})
MISSING_SHOWN = 15


def _disk_script_guids(project, root) -> tuple[set[str], bool]:
    """GUIDs of scripts outside the index: `.lotsmanignore`d folders under
    the indexed root, embedded `Packages/`, `Library/PackageCache`; the flag
    says whether the package cache was there."""
    found: set[str] = set()
    cache = project / "Library" / "PackageCache"
    for base in (root, project / "Packages", cache):
        if base.is_dir():
            for meta in base.rglob("*.cs.meta"):
                try:
                    g = meta_guid(meta.read_bytes()[:400])
                except OSError:
                    continue
                if g:
                    found.add(g)
    return found, cache.is_dir()


def render_missing(store, root) -> str:
    from lotsman import csdefines
    from lotsman.changeview import _family, _owners_of
    project = csdefines.project_dir(root)
    out: list[str] = []

    rows = store.asset_ref_keys(SCRIPT)
    known = store.guid_paths(sorted({r[0] for r in rows}))
    unknown = [r for r in rows if r[0] not in known]
    packages, has_cache = _disk_script_guids(project, root) if unknown else (set(), False)
    missing = [r for r in unknown if r[0] not in packages]
    by_guid: dict[str, list] = {}
    for guid, _, path, line, count in missing:
        by_guid.setdefault(guid, []).append((path, line, count))
    if by_guid and has_cache:
        out.append(f"missing scripts — {len(by_guid)} GUIDs used by assets match no script "
                   "in the project or its packages (the component shows as Missing):")
    elif by_guid:
        out.append(f"unresolved scripts — {len(by_guid)} GUIDs used by assets match no script "
                   "in the index; without Library/PackageCache package scripts cannot be told "
                   "apart from missing ones:")
    for guid, sites in sorted(by_guid.items(),
                              key=lambda x: -len(x[1]))[:MISSING_SHOWN if has_cache else 3]:
        where = ", ".join(f"{p}:{ln}" for p, ln, _ in sites[:3]) + (" …" if len(sites) > 3 else "")
        out.append(f"  {guid} in {len(sites)} assets: {where}")
    if by_guid and not has_cache and len(by_guid) > 3:
        out.append(f"  … {len(by_guid) - 3} more (open the project in Unity once to fill "
                   "Library/PackageCache, then rerun)")

    calls = store.asset_ref_keys(CALL)
    resolved = _owner_names(store, sorted({c[1] for c in calls}))
    families: dict[str, set[str]] = {}

    def family(t: str) -> set[str]:
        if t not in families:
            supers, subs = _family(store, t)
            families[t] = {t} | supers | {n for n, _, _ in subs}
        return families[t]

    stale, targetless = [], []
    for key, owner, path, line, count in calls:
        if owner.startswith("X:"):
            targetless.append((f"{owner[2:]}.{key}" if owner[2:] else key, path, line))
            continue
        if not owner:
            continue  # a GameObject target: the method may be on any component
        types = {t for t in resolved.get(owner, set()) if store.symbols_named(t)}
        if not types or key in UNITY_INHERITED:
            continue  # Unity's or a package's type: not in the index
        fam = set().union(*(family(t) for t in types))
        if not any(_owners_of(store, r) & fam for r in store.symbols_named(key)):
            stale.append((key, "/".join(sorted(types)), path, line))
    if stale:
        out.append(f"stale UnityEvent calls — {len(stale)} name a member the target type and its "
                   "indexed bases do not define (renamed or removed; fails silently at runtime):")
        out += [f"  {p}:{ln}  {t}.{k}" for k, t, p, ln in stale[:MISSING_SHOWN]]
    if targetless:
        out.append(f"UnityEvent calls without a target object — {len(targetless)} (the object "
                   "was deleted; the call does nothing):")
        out += [f"  {p}:{ln}  {k}" for k, p, ln in targetless[:MISSING_SHOWN]]

    anims = store.asset_ref_keys(ANIM)
    orphan: dict[str, list] = {}
    for key, _, path, line, _ in anims:
        if not store.symbols_named(key):
            orphan.setdefault(key, []).append((path, line))
    if orphan:
        out.append(f"animation events naming no method in the index — {len(orphan)} names "
                   "(Unity logs 'has no receiver' when the clip plays):")
        for key, sites in sorted(orphan.items())[:MISSING_SHOWN]:
            out.append(f"  {key}: {sites[0][0]}:{sites[0][1]}"
                       + (f" +{len(sites) - 1} more" if len(sites) > 1 else ""))
    if not out:
        return ("no broken references found: every script GUID resolves, UnityEvent calls "
                "name existing members, animation events name indexed methods")
    return "\n".join(out)
