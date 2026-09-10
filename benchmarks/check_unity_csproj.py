#!/usr/bin/env python3
"""A1 check: lotsman's Unity assembly model vs the IDE's generated `.csproj`.

Unity (through the Rider / VS integration package) writes one `.csproj` per
assembly into the project root: `<Compile Include>` lists the files it
compiles, `<ProjectReference>` the assemblies it references. For every
indexed C# file this compares lotsman's assembly with the csproj that
compiles it, and for every assembly both know, the referenced assemblies
(restricted to assemblies in the index: vendor folders excluded by
`.lotsmanignore` are invisible to lotsman by design).

The csproj files reflect the Editor's last project generation — stale
leftovers (a csproj without asmdef) are reported, not counted.

Usage:
    LOTSMAN_INDEX=/tmp/x.db python benchmarks/check_unity_csproj.py --project PATH [--assets Assets]
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lotsman import indexer, unity  # noqa: E402

COMPILE_RE = re.compile(r'<Compile Include="([^"]+)"')
PROJREF_RE = re.compile(r'<ProjectReference Include="([^"]+)\.csproj"')
NAME_RE = re.compile(r"<AssemblyName>([^<]+)</AssemblyName>")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--project", required=True)
    ap.add_argument("--assets", default="Assets", help="indexed folder (default Assets)")
    ap.add_argument("--show", type=int, default=10, help="mismatches printed per kind")
    args = ap.parse_args()
    project = Path(args.project).resolve()
    root = project / args.assets if args.assets != "." else project
    prefix = "" if root == project else root.relative_to(project).as_posix() + "/"
    store = indexer.open_store(root)
    indexer.index_repo(root, store)
    model = unity.load(store)
    if model is None:
        sys.exit("not a Unity project")
    indexed = {p for p in store.known_files() if p.endswith(".cs")}

    # A predefined assembly without sources is never built; its csproj may
    # linger empty.
    empty = {c.stem for c in project.glob("*.csproj")
             if "<Compile Include" not in c.read_text(encoding="utf-8", errors="replace")}
    files_ok, files_bad, refs_ok, refs_bad, stale, compared = 0, [], 0, [], [], set()
    for csproj in sorted(project.glob("*.csproj")):
        text = csproj.read_text(encoding="utf-8", errors="replace")
        m = NAME_RE.search(text)
        name = m.group(1) if m else csproj.stem
        asm = model.by_name.get(name)
        compiled = [p.replace("\\", "/") for p in COMPILE_RE.findall(text)]
        compiled = [p[len(prefix):] for p in compiled if p.startswith(prefix) and p.endswith(".cs")]
        mine = [p for p in compiled if p in indexed]
        if name in empty:
            continue
        if asm is None:
            if mine:
                stale.append(f"{csproj.name}: {len(mine)} indexed files, no asmdef named {name}")
            continue
        compared.add(name)
        for path in mine:
            got = model.assembly_for(path)
            if got and got[0].name == name:
                files_ok += 1
            else:
                files_bad.append(f"{path}: csproj {name}, lotsman {got[0].name if got else None}")
        want = {r for r in PROJREF_RE.findall(text) if r in model.by_name}
        if asm.predefined:
            have = (set(unity.PREDEFINED_SEES[name]) - empty) | {
                a.name for a in model.by_name.values()
                if not a.predefined and name in model.auto_users(a)}
        else:
            have = {t for _, _, t in model.references(asm) if t}
        if want == have:
            refs_ok += 1
        else:
            refs_bad.append(f"{name}: csproj-only {sorted(want - have)}, "
                            f"lotsman-only {sorted(have - want)}")
    asmdefs = [a for a in model.by_name.values() if not a.predefined]
    missing = sorted(a.name for a in asmdefs if a.name not in compared)
    print(f"asmdefs in index: {len(asmdefs)}; with csproj: {len(asmdefs) - len(missing)}"
          + (f"; no csproj: {', '.join(missing)}" if missing else ""))
    print(f"file -> assembly: {files_ok} match, {len(files_bad)} differ")
    for line in files_bad[:args.show]:
        print("  " + line)
    print(f"references: {refs_ok} assemblies match, {len(refs_bad)} differ")
    for line in refs_bad[:args.show]:
        print("  " + line)
    for line in stale:
        print("stale csproj (not counted): " + line)
    store.close()
    return 0 if not files_bad and not refs_bad else 1


if __name__ == "__main__":
    sys.exit(main())
