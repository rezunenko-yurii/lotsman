"""`lotsman unused [PATH]`: C# types and methods nothing names — candidates
for deletion, never proof.

A symbol is a candidate when
- no code names it outside its own body (the index's identifiers; bare
  camelCase names, which the index skips, by a text check);
- no asset uses it (Unity: the script's GUID in a prefab, scene or
  ScriptableObject; a UnityEvent call or animation event naming the method);
- no C# string literal names it (`Invoke("M")`, `SendMessage`, reflection).

Some code runs without being named. It is not listed, only counted by
reason: Unity messages and runtime conventions (`Dispose`, `ShouldSerializeX`),
overrides, public members of types implementing an interface the index does
not define, attributed code (`[MenuItem]`, `[Test]`, …), types created by
reflection (Unity's `Editor`, `PropertyDrawer`, …, or a base the code
enumerates: `GetTypesDerivedFrom<T>()`), Editor methods `-executeMethod` can
call, and the public API of packages (a folder with `package.json`).
Name-based: a name used anywhere counts for every symbol of that name.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from lotsman import callcheck, unityassets
from lotsman.extract import STOPWORDS
from lotsman.store import MAX_LINES_PER_ROW, TYPE_KINDS, Store, SymbolRow

SHOWN = 40

UNITY_MESSAGES = frozenset("""
Awake Start Update FixedUpdate LateUpdate OnEnable OnDisable OnDestroy OnValidate Reset
OnGUI OnApplicationFocus OnApplicationPause OnApplicationQuit OnBecameInvisible OnBecameVisible
OnCollisionEnter OnCollisionEnter2D OnCollisionExit OnCollisionExit2D OnCollisionStay
OnCollisionStay2D OnTriggerEnter OnTriggerEnter2D OnTriggerExit OnTriggerExit2D OnTriggerStay
OnTriggerStay2D OnControllerColliderHit OnJointBreak OnJointBreak2D OnMouseDown OnMouseDrag
OnMouseEnter OnMouseExit OnMouseOver OnMouseUp OnMouseUpAsButton OnParticleCollision
OnParticleSystemStopped OnParticleTrigger OnParticleUpdateJobScheduled OnPostRender OnPreCull
OnPreRender OnRenderImage OnRenderObject OnWillRenderObject OnTransformChildrenChanged
OnTransformParentChanged OnBeforeTransformParentChanged OnRectTransformDimensionsChange
OnRectTransformRemoved OnCanvasGroupChanged OnCanvasHierarchyChanged
OnDidApplyAnimationProperties OnAnimatorIK OnAnimatorMove OnAudioFilterRead OnDrawGizmos
OnDrawGizmosSelected OnServerInitialized OnConnectedToServer
OnInspectorGUI OnSceneGUI OnHeaderGUI OnPreviewGUI HasPreviewGUI OnPreviewSettings
OnInteractivePreviewGUI RenderStaticPreview CreateInspectorGUI GetPreviewTitle
OnFocus OnLostFocus OnHierarchyChange OnProjectChange OnSelectionChange OnInspectorUpdate
CreateGUI ShowButton AddItemsToMenu OnWizardCreate OnWizardUpdate OnWizardOtherButton
DrawWizardGUI OnPreprocessAsset OnPreprocessTexture OnPostprocessTexture OnPreprocessModel
OnPostprocessModel OnPreprocessAudio OnPostprocessAudio OnPostprocessAllAssets
OnPreprocessAnimation OnPostprocessAnimation OnPostprocessGameObjectWithUserProperties
OnPostprocessGameObjectWithAnimatedUserProperties OnPostprocessMaterial
OnPreprocessMaterialDescription OnPostprocessSprites OnAssignMaterialModel OnPostprocessPrefab
OnPreprocessSpeedTree OnPostprocessSpeedTree OnPostprocessCubemap OnPostprocessMeshHierarchy
OnPreprocessCameraDescription OnPreprocessLightDescription GetPostprocessOrder GetVersion
OnWillCreateAsset OnWillDeleteAsset OnWillMoveAsset OnWillSaveAssets IsOpenForEdit
OnStatusUpdated FileModeChanged MakeEditable CanOpenForEdit
OnStateEnter OnStateExit OnStateUpdate OnStateMove OnStateIK OnStateMachineEnter
OnStateMachineExit OnBeforeSerialize OnAfterDeserialize
OnPointerClick OnPointerDown OnPointerUp OnPointerEnter OnPointerExit OnPointerMove
OnBeginDrag OnDrag OnEndDrag OnDrop OnScroll OnSelect OnDeselect OnMove OnSubmit OnCancel
OnUpdateSelected OnInitializePotentialDrag
OnPreprocessBuild OnPostprocessBuild OnProcessScene OnActiveBuildTargetChanged
OnProcessShader OnProcessComputeShader OnFilterAssemblies OnPostBuildPlayerScriptDLLs
callbackOrder
""".split())

# Names the C# runtime, compiler or serializers use by convention, never by
# name (`IsExternalInit` enables `init`; Json.NET calls `ShouldSerializeX`).
RUNTIME_NAMES = frozenset("""
Main Dispose DisposeAsync GetEnumerator GetAsyncEnumerator MoveNext MoveNextAsync Current
GetAwaiter GetResult IsCompleted OnCompleted UnsafeOnCompleted Deconstruct Equals
GetHashCode ToString CompareTo Finalize GetObjectData IsExternalInit
""".split())
RUNTIME_PREFIXES = ("ShouldSerialize",)

# Base types whose subclasses Unity finds by reflection (Editor callbacks,
# importers, build hooks): never named by the code that runs them.
REFLECTED_BASES = frozenset("""
Editor EditorWindow PropertyDrawer DecoratorDrawer AssetPostprocessor ScriptableWizard
SettingsProvider AssetModificationProcessor ShaderGUI MaterialPropertyDrawer ScriptedImporter
AssetImporterEditor ScriptedImporterEditor IPreprocessBuildWithReport
IPostprocessBuildWithReport IProcessSceneWithReport IPreprocessBuild IPostprocessBuild
IProcessScene IActiveBuildTargetChanged IPreprocessShaders IPreprocessComputeShaders
IFilterBuildAssemblies IPostBuildPlayerScriptDLLs IUnityLinkerProcessor
IPostGenerateGradleAndroidProject BuildPlayerProcessor
OdinEditor OdinEditorWindow OdinValueDrawer OdinAttributeDrawer
""".split())

# Attributes that do not make code run: anything else ([MenuItem], [Test],
# [RuntimeInitializeOnLoadMethod], [Preserve], [UsedImplicitly], …) may.
INERT_ATTRIBUTES = frozenset("""
Obsolete Conditional MethodImpl DebuggerStepThrough DebuggerHidden DebuggerNonUserCode
DebuggerDisplay Pure NotNull CanBeNull NotNullWhen MaybeNull MustUseReturnValue Serializable
SerializeField Header Tooltip Range TextArea Multiline Space HideInInspector Min Delayed
ColorUsage GradientUsage DisallowMultipleComponent RequireComponent AddComponentMenu
ExecuteInEditMode ExecuteAlways SelectionBase CreateAssetMenu Flags HelpURL Icon MovedFrom
FormerlySerializedAs DefaultExecutionOrder ContextMenuItem InspectorName SuppressMessage
ExcludeFromCodeCoverage CompilerGenerated StructLayout FieldOffset NonSerialized
""".split())

# Skip reasons (shown as counts, never listed)
MESSAGE = "Unity messages and runtime conventions"
OVERRIDE = "overrides and explicit interface members"
EXTERNAL = "public members of types implementing an interface outside the index"
ATTRIBUTE = "attributed code"
REFLECTED = "types created by reflection (Unity bases, GetTypesDerivedFrom<T>…)"
PACKAGE = "public API of packages (package.json)"
EXECUTE = "Editor entry points for -executeMethod (public static void, no parameters)"
SKIP_ORDER = (MESSAGE, OVERRIDE, EXTERNAL, ATTRIBUTE, REFLECTED, EXECUTE, PACKAGE)

MAX_ATTRIBUTE_LINES = 40
MIN_SUFFIX = 10   # shortest name tail a string literal may name a type by
_PARENS_RE = re.compile(r"\([^()]*\)")
_ATTR_NAME_RE = re.compile(r"(?:^|,)\s*(?:\w+\s*:\s*)?([A-Za-z_][\w.]*)")
_STRING_RE = re.compile(r'"((?:[^"\\\n]|\\.)*)"')
_NAME_LIST_RE = re.compile(r"[\w.+]+(?:,\s*[\w.]+)*")
_EXTERNAL_IFACE_RE = re.compile(r"I[A-Z]")
_EXECUTE_RE = re.compile(r"\bpublic\s+static\s+void\s+\w+\s*\(\s*\)")
# A base type code enumerates by reflection: `TypeCache.GetTypesDerivedFrom<T>()`,
# `FindTypesImplementingInterface<T>()`, `FindAllSubclass(typeof(T))`,
# `typeof(T).IsAssignableFrom(t)`, `t.IsSubclassOf(typeof(T))`.
_DISCOVERY = r"\w*(?:Types|Subclass|Derived|Implement|Inherit|Classes)\w*"
_REFLECT_RE = re.compile(
    rf"\b{_DISCOVERY}\s*<\s*(?:\w+\.)*(\w+)\s*>"
    rf"|\b{_DISCOVERY}\s*\(\s*typeof\s*\(\s*(?:\w+\.)*(\w+)\s*\)"
    r"|typeof\s*\(\s*(?:\w+\.)*(\w+)\s*\)\s*\.\s*IsAssignableFrom"
    r"|IsSubclassOf\s*\(\s*typeof\s*\(\s*(?:\w+\.)*(\w+)")


@dataclass
class Candidate:
    row: SymbolRow
    members: int = 0      # methods and nested types folded into a type
    note: str = ""


@dataclass
class UnusedReport:
    scope: str
    candidates: list[Candidate] = field(default_factory=list)
    skipped: Counter = field(default_factory=Counter)
    attributes: Counter = field(default_factory=Counter)
    external: Counter = field(default_factory=Counter)
    used: Counter = field(default_factory=Counter)   # how the rest were found used


def _public(r: SymbolRow) -> bool:
    sig = f" {r.signature} "
    return " public " in sig or " protected " in sig


def _trackable(name: str) -> bool:
    return 3 <= len(name) <= 64 and name not in STOPWORDS and name.isidentifier()


def _attributes(lines: list[str], row: SymbolRow) -> list[str]:
    """Attribute names between the previous member and a declaration's
    name (attributes span lines and hold strings with brackets)."""
    chunk = []
    i = row.line - 2
    while i >= 0 and len(chunk) < MAX_ATTRIBUTE_LINES:
        s = lines[i].strip()
        if s.endswith((";", "{", "}")) and not s.startswith("["):
            break
        chunk.append(lines[i])
        i -= 1
    own = lines[row.line - 1] if row.line - 1 < len(lines) else ""
    text = callcheck.clean_lines("\n".join(reversed(chunk)) + "\n" + own.split(row.name, 1)[0])
    names = []
    for block in _bracket_blocks(" ".join(text)):
        prev = None
        while prev != block:
            prev, block = block, _PARENS_RE.sub("", block)
        for m in _ATTR_NAME_RE.finditer(block):
            name = m.group(1).rsplit(".", 1)[-1]
            names.append(name[:-9] if name.endswith("Attribute") and len(name) > 9 else name)
    return names


def _bracket_blocks(text: str) -> list[str]:
    """Contents of top-level `[...]` blocks."""
    out, depth, start = [], 0, 0
    for i, ch in enumerate(text):
        if ch == "[":
            if depth == 0:
                start = i + 1
            depth += 1
        elif ch == "]" and depth:
            depth -= 1
            if depth == 0:
                out.append(text[start:i])
    return out


def _camel_suffixes(name: str) -> list[str]:
    """`JsonLoaderProbeTail` -> `LoaderProbeTail`, `ProbeTail`, … (long ones
    only): type lookups by a short name match the end of a full name."""
    return [name[i:] for i in range(1, len(name) - MIN_SUFFIX + 1)
            if name[i].isupper() and not name[i - 1].isupper()]


class _Files:
    """Source lines of C# files, read once."""

    def __init__(self, root: Path):
        self.root = root
        self.raw: dict[str, list[str]] = {}
        self.clean: dict[str, list[str]] = {}

    def lines(self, path: str) -> list[str]:
        if path not in self.raw:
            try:
                text = (self.root / path).read_text(encoding="utf-8", errors="replace")
            except OSError:
                text = ""
            self.raw[path] = text.split("\n")
        return self.raw[path]

    def cleaned(self, path: str) -> list[str]:
        if path not in self.clean:
            self.clean[path] = callcheck.clean_lines("\n".join(self.lines(path)))
        return self.clean[path]


def _innermost(types: list[SymbolRow], line: int, exclude: SymbolRow | None = None
               ) -> SymbolRow | None:
    enclosing = [t for t in types if t is not exclude and t.line <= line <= t.end_line]
    return max(enclosing, key=lambda t: t.line) if enclosing else None


def collect_unused(store: Store, root: Path, scope: str = "") -> UnusedReport:
    scope = scope.strip().removeprefix("./").rstrip("/")
    rep = UnusedReport(scope)
    rows = store.symbols_of_lang("csharp", TYPE_KINDS + ("method",))
    by_file: dict[str, list[SymbolRow]] = defaultdict(list)
    for r in rows:
        by_file[r.path].append(r)
    types_in = {p: [r for r in rs if r.kind in TYPE_KINDS] for p, rs in by_file.items()}
    project_types = {r.name for r in rows if r.kind in TYPE_KINDS}

    def in_scope(path: str) -> bool:
        return not scope or path == scope or path.startswith(scope + "/")

    bases: dict[str, set[str]] = defaultdict(set)
    for t, b in store.all_bases():
        bases[t].add(b)
    supers_memo: dict[str, set[str]] = {}

    def supers(name: str) -> set[str]:
        if name not in supers_memo:
            out: set[str] = set()
            frontier = {name}
            for _ in range(8):
                nxt = {b for f in frontier for b in bases.get(f, ())} - out - {name}
                if not nxt:
                    break
                out |= nxt
                frontier = nxt
            supers_memo[name] = out
        return supers_memo[name]

    packages = sorted(p.rsplit("/", 1)[0] if "/" in p else ""
                      for p in store.paths_named("package.json"))
    packages = [p for p in packages if p]
    files = _Files(root)

    # 1. Named by code or assets: used.
    live: list[tuple[SymbolRow, SymbolRow | None]] = []
    for path, frows in by_file.items():
        if not in_scope(path):
            continue
        tlist = types_in[path]
        for r in frows:
            owner = _innermost(tlist, r.line, exclude=r)
            if r.kind == "method" and owner is not None and r.name == owner.name:
                continue  # constructor / finalizer: part of its type
            if _trackable(r.name) and "operator " not in r.signature:
                live.append((r, owner))
    sites = store.ident_lines(sorted({r.name for r, _ in live}))
    called = store.asset_keys(unityassets.CALL) | store.asset_keys(unityassets.ANIM)
    script_keys = store.asset_keys(unityassets.SCRIPT)
    guids = store.meta_guids() if script_keys else {}
    unnamed: list[tuple[SymbolRow, SymbolRow | None]] = []
    for r, owner in live:
        how = _code_use(r, sites.get(r.name, ()))
        if not how and r.kind == "method" and r.name in called:
            how = "asset"
        if not how and r.kind in TYPE_KINDS and guids.get(r.path) in script_keys \
                and Path(r.path).stem == r.name:
            how = "asset"
        if how:
            rep.used[how] += 1
        else:
            unnamed.append((r, owner))

    # 2. Unnamed, but may run without a name: counted, not listed. A type
    # holding attributed members ([Test], [MenuItem]) or -executeMethod entry
    # points goes with them.
    unused: list[SymbolRow] = []
    attributed_types: set[int] = set()
    for r, owner in unnamed:
        reason = _skip_reason(r, owner, files, supers, project_types, packages, rep)
        if reason:
            rep.skipped[reason] += 1
            if reason in (ATTRIBUTE, EXECUTE) and owner is not None:
                attributed_types.add(id(owner))
        else:
            unused.append(r)
    rep.skipped[ATTRIBUTE] += sum(1 for r in unused if id(r) in attributed_types)
    unused = [r for r in unused if id(r) not in attributed_types]
    unused, discovered = _text_uses(unused, store, files, rep)
    discovered &= project_types
    if discovered:
        found = [r for r in unused if r.kind in TYPE_KINDS and supers(r.name) & discovered]
        rep.skipped[REFLECTED] += len(found)
        unused = [r for r in unused if r not in found]

    # 3. A type is used when a nested type is, and a static class when its
    # members are (extension methods are called without the class name).
    unused_ids = {id(r) for r in unused}
    kept = []
    for r in unused:
        if r.kind in TYPE_KINDS:
            static = " static " in f" {r.signature} "
            inner = [m for m in by_file[r.path] if r.line < m.line <= r.end_line
                     and id(m) not in unused_ids
                     and (m.kind in TYPE_KINDS or static and m.kind == "method")]
            if inner:
                rep.used["members"] += 1
                continue
        kept.append(r)

    # 4. Fold members into their unused type.
    kept_types = [r for r in kept if r.kind in TYPE_KINDS]
    out: list[Candidate] = []
    for r in kept:
        outer = [t for t in kept_types if t is not r and t.path == r.path
                 and t.line <= r.line <= t.end_line]
        if outer:
            continue
        cand = Candidate(r)
        if r.kind in TYPE_KINDS:
            cand.members = sum(1 for m in by_file[r.path] if m is not r
                               and r.line < m.line <= r.end_line
                               and not (m.kind == "method" and m.name == r.name))
            # A component / ScriptableObject: its chain ends in an engine class.
            engine = [b for b in supers(r.name)
                      if b not in project_types and not _EXTERNAL_IFACE_RE.match(b)]
            if engine and guids.get(r.path) and Path(r.path).stem == r.name:
                cand.note = "no prefab, scene or asset uses the script"
        out.append(cand)
    rep.candidates = out
    return rep


def _skip_reason(r: SymbolRow, owner: SymbolRow | None, files: _Files, supers,
                 project_types: set[str], packages: list[str], rep: UnusedReport) -> str:
    sig = f" {r.signature} "
    if r.name in RUNTIME_NAMES or r.name.startswith(RUNTIME_PREFIXES):
        return MESSAGE
    if r.kind == "method":
        if r.name in UNITY_MESSAGES:
            return MESSAGE
        if " override " in sig or re.search(r"\.\s*" + re.escape(r.name) + r"\b", r.signature):
            return OVERRIDE
    attrs = [a for a in _attributes(files.lines(r.path), r) if a not in INERT_ATTRIBUTES]
    if attrs:
        rep.attributes.update(attrs)
        return ATTRIBUTE
    if r.kind in TYPE_KINDS:
        found = supers(r.name) & REFLECTED_BASES
        if found:
            return REFLECTED
    public = " public " in sig or " protected " in sig
    if r.kind == "method" and owner is not None:
        public = public or " interface " in f" {owner.signature} "
        ext = sorted(s for s in supers(owner.name)
                     if s not in project_types and _EXTERNAL_IFACE_RE.match(s)) if public else []
        if ext:
            rep.external.update(ext)
            return EXTERNAL
    if r.kind == "method" and _EXECUTE_RE.search(r.signature) \
            and ("/Editor/" in f"/{r.path}"):
        return EXECUTE
    if public and any(r.path.startswith(p + "/") for p in packages):
        return PACKAGE
    return ""


def _code_use(r: SymbolRow, sites) -> str:
    for path, lines in sites:
        if path != r.path:
            return "code"
        if len(lines) >= MAX_LINES_PER_ROW or any(not r.line <= ln <= r.end_line
                                                   for ln in lines):
            return "code"
    return ""


def _text_uses(cands: list[SymbolRow], store: Store, files: _Files,
               rep: UnusedReport) -> tuple[list[SymbolRow], set[str]]:
    """Drop candidates a C# string literal names (`Invoke("M")`, reflection)
    and camelCase / _underscore ones named as bare words (the index skips
    those). Also returns the base types code enumerates by reflection."""
    if not cands:
        return cands, set()
    names = {r.name for r in cands}
    tails: dict[str, set[str]] = defaultdict(set)
    for r in cands:
        if r.kind in TYPE_KINDS:
            for tail in _camel_suffixes(r.name):
                tails[tail].add(r.name)
    camel = {r.name for r in cands if not r.name[:1].isupper()}
    in_strings: set[str] = set()
    discovered: set[str] = set()
    bare: dict[str, list[tuple[str, int]]] = defaultdict(list)
    for path in store.paths_of_lang("csharp"):
        lines = files.lines(path)
        text = "\n".join(lines)
        for m in _STRING_RE.finditer(text):
            s = m.group(1)
            if _NAME_LIST_RE.fullmatch(s):
                for w in re.split(r"[.+,\s]+", s):
                    if w in names:
                        in_strings.add(w)
                    in_strings.update(tails.get(w, ()))
        if "typeof" in text or "<" in text:
            for m in _REFLECT_RE.finditer(text):
                discovered.update(g for g in m.groups() if g)
        hits = [n for n in camel if n in text]
        if hits:
            clean = files.cleaned(path)
            for n in hits:
                pat = re.compile(r"\b" + re.escape(n) + r"\b")
                bare[n] += [(path, i + 1) for i, ln in enumerate(clean) if pat.search(ln)]
    out = []
    for r in cands:
        if r.name in in_strings:
            rep.used["string"] += 1
        elif any(p != r.path or not r.line <= ln <= r.end_line for p, ln in bare.get(r.name, ())
                 if not (p == r.path and ln == r.line)):
            rep.used["text"] += 1
        else:
            out.append(r)
    return out, discovered


def render_unused(rep: UnusedReport, limit: int = SHOWN) -> str:
    types = sum(1 for c in rep.candidates if c.row.kind in TYPE_KINDS)
    methods = len(rep.candidates) - types
    nfiles = len({c.row.path for c in rep.candidates})
    hidden = sum(1 for c in rep.candidates if not _public(c.row))
    where = f" under {rep.scope}/" if rep.scope else ""
    lines = [f"unused candidates{where} — {types} types, {methods} methods in {nfiles} files "
             f"({hidden} not public): nothing in code, assets or string literals names them "
             "(name-based; verify before deleting)"]
    shown = 0
    last = None
    for c in sorted(rep.candidates, key=lambda c: (c.row.path, c.row.line)):
        if shown >= limit:
            break
        if c.row.path != last:
            lines.append(c.row.path)
            last = c.row.path
        extra = f" (+{c.members} members)" if c.members else ""
        note = f" — {c.note}" if c.note else ""
        lines.append(f"  {c.row.kind} {c.row.name} :{c.row.line}{extra}{note}")
        shown += 1
    if len(rep.candidates) > shown:
        lines.append(f"+ {len(rep.candidates) - shown} more (narrow with a path, or -k)")
    if not rep.candidates:
        lines.append("(none)")
    parts = []
    for reason in SKIP_ORDER:
        n = rep.skipped.get(reason)
        if not n:
            continue
        detail = ""
        if reason == ATTRIBUTE and rep.attributes:
            detail = " (" + ", ".join(f"[{a}] {k}" for a, k in rep.attributes.most_common(4)) + ")"
        elif reason == EXTERNAL and rep.external:
            detail = " (" + ", ".join(a for a, _ in rep.external.most_common(3)) + ")"
        parts.append(f"{n} {reason}{detail}")
    if parts:
        lines.append("also named by nothing, but may run without a name (not listed): "
                     + "; ".join(parts))
    return "\n".join(lines)


def unused_json(rep: UnusedReport) -> dict:
    return {
        "scope": rep.scope,
        "candidates": [{"path": c.row.path, "name": c.row.name, "kind": c.row.kind,
                        "line": c.row.line, "members": c.members, "public": _public(c.row),
                        **({"note": c.note} if c.note else {})} for c in rep.candidates],
        "not_listed": {r: rep.skipped[r] for r in SKIP_ORDER if rep.skipped.get(r)},
    }
