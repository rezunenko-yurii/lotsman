"""Unity project facts, offline: which assembly compiles a file and what that
assembly can see.

Assemblies come from `.asmdef` / `.asmref` files in the index (GUIDs from the
sibling `.meta`); files without one fall into Unity's predefined assemblies
(`Assembly-CSharp`, `-firstpass` for `Plugins/` and `Standard Assets/`,
`-Editor` for `Editor/` folders). Asmdef references are not transitive: an
assembly sees only what it lists, plus nothing else — the predefined
assemblies are the exception and see every auto-referenced asmdef.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from lotsman import csdefines

ASMDEF_LANG = "asmdef"
META_UNITY_ASSETS = "unity_assets"  # meta key: "" (repo is Assets/) or "Assets/"

CSHARP = "Assembly-CSharp"
FIRSTPASS = "Assembly-CSharp-firstpass"
EDITOR = "Assembly-CSharp-Editor"
EDITOR_FIRSTPASS = "Assembly-CSharp-Editor-firstpass"
PREDEFINED = (CSHARP, FIRSTPASS, EDITOR, EDITOR_FIRSTPASS)
FIRSTPASS_DIRS = ("Plugins/", "Standard Assets/", "Pro Standard Assets/")
# What each predefined assembly sees besides auto-referenced asmdefs.
PREDEFINED_SEES = {
    CSHARP: (FIRSTPASS,),
    FIRSTPASS: (),
    EDITOR: (CSHARP, FIRSTPASS, EDITOR_FIRSTPASS),
    EDITOR_FIRSTPASS: (FIRSTPASS,),
}
TEST_RUNNERS = ("UnityEngine.TestRunner", "UnityEditor.TestRunner")
_GUID_RE = re.compile(rb"^guid:\s*([0-9a-fA-F]{32})", re.M)


def parse_asmdef(data: bytes, meta: bytes | None) -> dict | None:
    """Stored form of one `.asmdef`/`.asmref`: its JSON plus the line of each
    top-level key and of each `references` entry, and the GUID from `.meta`."""
    try:
        obj = json.loads(data.decode("utf-8-sig", errors="replace"))
    except ValueError:
        return None
    if not isinstance(obj, dict):
        return None
    lines = data.decode("utf-8-sig", errors="replace").splitlines()
    key_lines: dict[str, int] = {}
    for n, text in enumerate(lines, 1):
        m = re.match(r'\s*"(\w+)"\s*:', text)
        if m and m.group(1) in obj and m.group(1) not in key_lines:
            key_lines[m.group(1)] = n
    ref_lines = []
    start = key_lines.get("references", 0)
    for ref in obj.get("references") or []:
        at = next((n for n in range(start, len(lines) + 1)
                   if f'"{ref}"' in lines[n - 1]), start)
        ref_lines.append(at)
        start = at
    guid = _GUID_RE.search(meta or b"")
    return {"json": obj, "lines": key_lines, "ref_lines": ref_lines,
            "guid": guid.group(1).decode().lower() if guid else ""}


@dataclass
class Assembly:
    name: str
    path: str = ""  # asmdef, repo-relative; "" for predefined assemblies
    guid: str = ""
    references: list[str] = field(default_factory=list)  # raw: name or GUID:x
    ref_lines: list[int] = field(default_factory=list)
    include: list[str] = field(default_factory=list)
    exclude: list[str] = field(default_factory=list)
    constraints: list[str] = field(default_factory=list)
    version_defines: list[dict] = field(default_factory=list)
    auto_referenced: bool = True
    no_engine: bool = False
    lines: dict[str, int] = field(default_factory=dict)

    @property
    def predefined(self) -> bool:
        return not self.path

    @property
    def editor_only(self) -> bool:
        if self.predefined:
            return self.name in (EDITOR, EDITOR_FIRSTPASS)
        return self.include == ["Editor"]

    @property
    def test(self) -> bool:
        return (any(r in TEST_RUNNERS for r in self.references)
                or "UNITY_INCLUDE_TESTS" in self.constraints)

    def line(self, key: str) -> int:
        return self.lines.get(key, 0)  # 0: key absent

    def at(self, key: str, full: bool = True) -> str:
        """`path:line` of a key (just the path when the key is absent)."""
        path = self.path if full else self.path.rpartition("/")[2]
        return f"{path}:{self.line(key)}" if self.line(key) else path


class UnityProject:
    """Assembly model of one indexed Unity project (built from the store)."""

    def __init__(self, rows: list[tuple[str, str, str, str]], assets_prefix: str):
        self.assets_prefix = assets_prefix
        self.by_name: dict[str, Assembly] = {}
        self.by_guid: dict[str, str] = {}
        self.dirs: dict[str, str] = {}  # folder -> assembly name (or raw ref for asmref)
        asmrefs: list[tuple[str, str]] = []
        for path, kind, name, data in sorted(rows):
            folder = path.rpartition("/")[0]
            info = json.loads(data)
            obj = info["json"]
            if kind == "asmref":
                asmrefs.append((folder, str(obj.get("reference") or "")))
                continue
            asm = Assembly(
                name=name, path=path, guid=info.get("guid", ""),
                references=[str(r) for r in obj.get("references") or []],
                ref_lines=info.get("ref_lines", []),
                include=list(obj.get("includePlatforms") or []),
                exclude=list(obj.get("excludePlatforms") or []),
                constraints=list(obj.get("defineConstraints") or []),
                version_defines=list(obj.get("versionDefines") or []),
                auto_referenced=obj.get("autoReferenced", True) is not False,
                no_engine=bool(obj.get("noEngineReferences")),
                lines=info.get("lines", {}))
            self.by_name.setdefault(name, asm)
            if asm.guid:
                self.by_guid[asm.guid] = name
            self.dirs.setdefault(folder, name)
        for folder, ref in asmrefs:
            target = self.resolve(ref)
            if target:
                self.dirs.setdefault(folder, target)
        for name in PREDEFINED:
            self.by_name.setdefault(name, Assembly(name=name))

    def resolve(self, ref: str) -> str | None:
        """Assembly name for a reference entry (`Name` or `GUID:hex`)."""
        if ref.startswith("GUID:"):
            return self.by_guid.get(ref[5:].lower())
        return ref if ref in self.by_name else None

    def assembly_for(self, path: str) -> tuple[Assembly, str] | None:
        """(assembly, why) for a repo-relative source path; None when the
        file is outside anything Unity compiles (e.g. a package folder
        without asmdef)."""
        folder = path.rpartition("/")[0]
        while True:
            if folder in self.dirs:
                return self.by_name[self.dirs[folder]], ""
            if not folder:
                break
            folder = folder.rpartition("/")[0]
        if not path.startswith(self.assets_prefix):
            return None
        rel = path[len(self.assets_prefix):]
        firstpass = rel.startswith(FIRSTPASS_DIRS)
        editor = "Editor" in rel.split("/")[:-1]
        name = ((EDITOR_FIRSTPASS if firstpass else EDITOR) if editor
                else (FIRSTPASS if firstpass else CSHARP))
        why = ("no asmdef: " + " + ".join(
            [w for w, on in (("Plugins/ or Standard Assets/", firstpass),
                             ("Editor folder", editor)) if on])).rstrip(": ")
        return self.by_name[name], why

    def references(self, asm: Assembly) -> list[tuple[str, int, str | None]]:
        """(raw entry, asmdef line, resolved name or None) per reference."""
        lines = asm.ref_lines + [asm.line("references")] * len(asm.references)
        return [(raw, lines[i], self.resolve(raw)) for i, raw in enumerate(asm.references)]

    def referenced_by(self, asm: Assembly) -> list[tuple[Assembly, int]]:
        """Asmdefs listing `asm` directly, with the line of the entry."""
        out = []
        for other in self.by_name.values():
            for _, line, target in self.references(other):
                if target == asm.name:
                    out.append((other, line))
                    break
        return sorted(out, key=lambda x: x[0].name)

    def auto_users(self, asm: Assembly) -> list[str]:
        """Predefined assemblies that see `asm` without listing it."""
        if asm.predefined:
            return [p for p, sees in PREDEFINED_SEES.items() if asm.name in sees]
        # Editor-only asmdefs are auto-referenced by the runtime predefined
        # assemblies too, but only in the Editor compile (the generated
        # csproj lists them); player builds drop them — see editor_gap().
        return list(PREDEFINED) if asm.auto_referenced else []

    @staticmethod
    def editor_gap(user: Assembly, owner: Assembly) -> bool:
        """`user` sees `owner` in the Editor, but a player build of `user`
        drops the Editor-only `owner` (compile errors in builds)."""
        return owner.editor_only and not user.editor_only

    def can_see(self, user: Assembly, owner: Assembly) -> bool | None:
        """Whether code in `user` compiles against types of `owner`; None when
        an unresolved GUID reference might be it."""
        if user.name == owner.name:
            return True
        if user.predefined:
            if owner.predefined:
                return owner.name in PREDEFINED_SEES[user.name]
            return user.name in self.auto_users(owner)
        refs = self.references(user)
        if any(target == owner.name for _, _, target in refs):
            return True
        if not owner.guid and any(t is None and r.startswith("GUID:") for r, _, t in refs):
            return None
        return False


def load(store) -> UnityProject | None:
    """The project's assembly model, or None outside Unity projects."""
    prefix = store.get_meta(META_UNITY_ASSETS)
    if prefix is None:
        return None
    return UnityProject(store.assembly_rows(), prefix)


def flags(asm: Assembly) -> str:
    """Compact traits: ` · Editor only · tests · platforms: iOS`."""
    parts = []
    if asm.editor_only:
        parts.append("Editor only")
    elif asm.include:
        parts.append("platforms: " + ", ".join(asm.include))
    if asm.exclude:
        parts.append("not on: " + ", ".join(asm.exclude))
    if asm.test:
        parts.append("tests")
    if asm.constraints:
        parts.append("defineConstraints: " + ", ".join(asm.constraints))
    if asm.no_engine:
        parts.append("noEngineReferences")
    if not asm.predefined and not asm.auto_referenced:
        parts.append("not autoReferenced")
    return "".join(f" · {p}" for p in parts)


def assembly_line(project: UnityProject, path: str) -> str | None:
    """One outline header line: the assembly compiling `path`."""
    found = project.assembly_for(path)
    if found is None:
        return None
    asm, why = found
    where = asm.path if not asm.predefined else f"({why})"
    return f"assembly: {asm.name} — {where}{flags(asm)}"


def _asm_ref(asm: Assembly, line: int) -> str:
    return f"{asm.name} — {asm.path}:{line}"


def render_assembly(project: UnityProject, asm: Assembly, why: str = "") -> list[str]:
    """Assembly section of `unity`: identity, references, who references it."""
    head = f"assembly: {asm.name} — {asm.path or '(' + why + ')'}{flags(asm)}"
    out = [head]
    if asm.predefined:
        auto = [a for a in project.by_name.values()
                if not a.predefined and asm.name in project.auto_users(a)]
        out.append("  sees: " + " + ".join([*PREDEFINED_SEES[asm.name],
                                            f"{len(auto)} autoReferenced asmdefs"])
                   + " (never non-autoReferenced ones)")
    else:
        refs = project.references(asm)
        at = asm.at("references", full=False)
        if not refs:
            out.append(f"  references ({at}): none")
        else:
            names = [(t or r[:13] + "… (not in index)")
                     + (" (GUID)" if r.startswith("GUID:") and t else "")
                     for r, _, t in refs]
            out.append(f"  references ({at}): " + ", ".join(names))
    users = project.referenced_by(asm)
    auto = project.auto_users(asm)
    if users or auto:
        out.append("  referenced by (direct; asmdef references are not transitive):")
        out += [f"    {_asm_ref(u, line)}" for u, line in users]
        if auto:
            out.append("    predefined " + ", ".join(auto)
                       + ("" if asm.predefined else " (autoReferenced"
                          + ("; runtime ones in the Editor only)" if asm.editor_only else ")")))
    else:
        out.append("  referenced by: nothing"
                   + ("" if asm.predefined else " (not autoReferenced, no asmdef lists it)"))
    return out


def render_visibility(project: UnityProject, user: Assembly, owner: Assembly,
                      what: str) -> str:
    """Verdict line for `unity FILE USES`: can `user` compile against `owner`?"""
    seen = project.can_see(user, owner)
    if seen and project.editor_gap(user, owner):
        return (f"{what} ({owner.name}, Editor only) is visible from {user.name} in the "
                "Editor only: player builds fail unless the use is under #if UNITY_EDITOR")
    if seen:
        return f"{what} ({owner.name}) is visible from {user.name}"
    if seen is None:
        return (f"{what} ({owner.name}): maybe visible from {user.name} — "
                "it lists a GUID reference outside the index")
    if user.predefined:
        fix = (f"set autoReferenced in {owner.at('autoReferenced')}, "
               "or move this code under an asmdef that references it"
               if not owner.predefined else f"{owner.name} compiles after {user.name}")
    elif owner.predefined:
        fix = "asmdef assemblies cannot reference predefined ones (Assembly-CSharp*)"
    else:
        fix = f'add "{owner.name}" to references in {user.at("references")}'
    return f"{what} ({owner.name}) is NOT visible from {user.name}: {fix}"


def _locate(store, project: UnityProject, target: str
            ) -> list[tuple[str, str, int, Assembly, str]]:
    """(label, file, line, assembly, why) for a file path, an assembly name,
    or a symbol name (each defining file, types first, at most 3)."""
    target = target.strip()
    if store.has_file(target):
        found = project.assembly_for(target)
        return [(target, target, 0, *found)] if found else []
    if target in project.by_name:
        asm = project.by_name[target]
        return [(asm.path or asm.name, "", 0, asm, "predefined" if asm.predefined else "")]
    rows = sorted(store.symbols_named(target.rpartition(".")[2]),
                  key=lambda r: (r.kind not in ("class", "type"), r.path))
    out, seen = [], set()
    for r in rows:
        found = project.assembly_for(r.path) if r.path.endswith(".cs") else None
        if found and r.path not in seen:
            seen.add(r.path)
            out.append((f"{r.path}:{r.line}  {r.signature}", r.path, r.line, *found))
    return out[:3]


MAX_COND_GROUPS = 12
MAX_COND_LINES = 20


def render_conditions(store, root, pd, asm: Assembly, path: str, line: int) -> list[str]:
    """Targets the assembly builds for, and the file's `#if` branches grouped
    by effective condition with a verdict per target."""
    directives = store.directives_in_file(path) if path else []
    ctx = csdefines.context_for(pd, asm, directives, root)
    targets = pd.targets
    built = {t: csdefines.compiled_on(asm, ctx, t) for t in targets}
    out = [f"compiled for: {csdefines.verdict(built)}"]
    branches = [d for d in directives if d.kind == "if"]
    if not branches:
        return out
    judge = lambda cond: csdefines.verdict(  # noqa: E731
        {t: (csdefines.evaluate(cond, lambda s, t=t: ctx.value(s, t)) if built[t] is not False
             else False) for t in targets})
    if line:
        here = csdefines.region_at(directives, line)
        if here:
            out.append(f"declared under #if {here.cond} → {judge(here.cond)}")
    editor = (f"; Editor = active target {pd.active} ({pd.active_source})" if pd.active
              else "; Editor's active target unknown")
    out.append(f"#if branches (effective condition → where it compiles{editor}):")
    groups: dict[str, list[int]] = {}
    for d in branches:
        groups.setdefault(d.cond, []).append(d.line)
    for cond, lines in list(groups.items())[:MAX_COND_GROUPS]:
        shown = ",".join(map(str, lines[:MAX_COND_LINES])) + ("…" if len(lines) > MAX_COND_LINES else "")
        out.append(f"  {cond} → {judge(cond)} — {len(lines)}× {path}:{shown}")
    if len(groups) > MAX_COND_GROUPS:
        out.append(f"  … {len(groups) - MAX_COND_GROUPS} more conditions")
    syms = [x for cond in groups for x in csdefines.symbols(cond)]
    nowhere = [x for x in dict.fromkeys(syms) if ctx.defined_nowhere(x)]
    project_syms = [x for x in dict.fromkeys(syms) if not csdefines.BUILTIN_RE.match(x)]
    if nowhere:
        out.append("defined nowhere: " + ", ".join(nowhere) + " (not in Player Settings "
                   "scriptingDefineSymbols, csc.rsp, asmdef versionDefines or #define — "
                   "false unless a build script sets it)")
    if project_syms:
        out += render_define_sites(pd)
    return out


def render_define_sites(pd) -> list[str]:
    """Where project define symbols come from (identical groups merged)."""
    if not pd.settings_path:
        sites = "Player Settings: no ProjectSettings.asset found"
    elif not pd.sites:
        sites = f"Player Settings: no scriptingDefineSymbols ({pd.settings_path})"
    else:
        merged: dict[tuple, list] = {}
        for site in pd.sites:
            merged.setdefault(tuple(site.symbols), []).append(site)
        sites = "; ".join(
            f"{group[0].source}:{','.join(str(x.line) for x in group)} "
            f"{', '.join(g for x in group for g in x.groups)} = {';'.join(syms) or '(empty)'}"
            for syms, group in merged.items())
    rsp = f"{pd.rsp_path} = {';'.join(sorted(pd.rsp))}" if pd.rsp_path else "csc.rsp: none"
    return [f"project defines: {sites}; {rsp}"]


def render_unity(store, target: str, uses: str | None = None, root=None,
                 log: str | None = None) -> str:
    """`unity TARGET [USES]`: the assembly compiling TARGET (file, type or
    assembly), its references and who references it, the targets it builds
    for and its `#if` branches per target; with USES, whether TARGET's
    assembly can see the assembly of USES. `unity errors`: compile errors of
    the last compilation in the Editor log (`log` overrides the location).
    `unity missing`: broken serialized references (unityassets)."""
    project = load(store)
    if project is None:
        return "(not a Unity project: no ProjectSettings/ at or above the indexed folder)"
    if target.strip() == "errors" and root is not None and not store.has_file("errors"):
        from lotsman import unitylog
        return unitylog.render_errors(store, root, log)
    if target.strip() == "missing" and root is not None and not store.has_file("missing"):
        from lotsman import unityassets
        return unityassets.render_missing(store, root)
    hits = _locate(store, project, target)
    if not hits:
        return f"(`{target}`: no indexed file, assembly or C# symbol by that name)"
    pd = csdefines.load_project(root) if root is not None else None
    out: list[str] = []
    for label, path, line, asm, why in hits:
        out += [label, *render_assembly(project, asm, why)]
        if pd is not None:
            out += render_conditions(store, root, pd, asm, path, line)
    if uses:
        owners = _locate(store, project, uses)
        if not owners:
            out.append(f"(`{uses}`: no indexed file, assembly or C# symbol by that name)")
        for label, _, _, owner, _ in owners:
            out.append(f"uses {label}")
            out += ["  " + render_visibility(project, hit[3], owner, uses) for hit in hits]
    return "\n".join(out)
