#!/usr/bin/env python3
"""Public Unity game corpus (G1): what serialized assets say vs what lotsman shows.

Corpus: Unity Open Project #1 "Chop Chop" (Apache-2.0), pinned commit, a
content-heavy 2020.3 project — ScriptableObject event channels, UnityEvents,
prefabs and scenes in plain git (no LFS). Only text files are fetched
(sparse, blob-filtered: ~340 MB instead of ~2 GB; no textures, audio, models).

Truth comes from the assets themselves: `.cs.meta` GUID → script;
`m_Script: {fileID: 11500000, guid: G}` in prefabs, scenes, ScriptableObject
assets → script used by that asset; UnityEvent persistent calls
(`m_MethodName` with `m_TargetAssemblyTypeName`, Unity 2020.2+) and
animation events (`functionName` in `.anim` and model `.meta` import
settings) → method called from data. It measures how
many of those asset users and data-called methods `refs` reports, and how
many types would look unused from code alone (the X1 trap).

Usage:
    python benchmarks/bench_unity_game.py [--project PATH] [--keep] [--db DIR]

Without --project the script fetches the pinned commit into a temp directory
(network required) and removes it afterwards unless --keep.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import tempfile
import time
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lotsman import indexer, refsview, unityassets, unused, why  # noqa: E402
from lotsman.store import TYPE_KINDS, Store  # noqa: E402

CORPUS_REPO = "https://github.com/UnityTechnologies/open-project-1.git"
CORPUS_COMMIT = "608eac98df29cd97821a6115cd52dfb9027345b1"  # main, 2022-06-26
PROJECT_SUBDIR = "UOP1_Project"
# Sparse checkout: every text format that can carry code references.
TEXT_PATTERNS = [
    "*.cs", "*.meta", "*.asmdef", "*.asmref", "*.rsp",
    "*.prefab", "*.unity", "*.asset", "*.anim", "*.controller", "*.overrideController",
    "*.playable", "*.signal", "*.mixer", "*.mat", "*.inputactions", "*.json", "*.txt",
]
ASSET_EXTS = (".prefab", ".unity", ".asset", ".anim", ".controller", ".overrideController",
              ".playable", ".signal", ".mixer")

GUID_RE = re.compile(r"^guid: ([0-9a-f]{32})", re.M)
SCRIPT_RE = re.compile(r"m_Script: \{fileID: 11500000, guid: ([0-9a-f]{32})")
TARGET_TYPE_RE = re.compile(r"m_TargetAssemblyTypeName: ([\w.+`]+)")
METHOD_RE = re.compile(r"m_MethodName: (\w+)")
ANIM_EVENT_RE = re.compile(r"functionName: (\w+)")
SOURCE_PREFAB_RE = re.compile(r"m_SourcePrefab: \{fileID: -?\d+, guid: ([0-9a-f]{32})")
# A UnityEvent call set by a prefab-instance override.
OVERRIDE_CALL_RE = re.compile(
    r"propertyPath: \S*m_PersistentCalls\.m_Calls\.Array\.data\[\d+\]\.m_MethodName\s*\n"
    r"\s*value: (\w+)")

GATES = {"min_scripts": 250, "min_asset_files": 1000, "min_script_users": 150,
         "min_data_methods": 20}


def fetch(dest: Path) -> Path:
    """Sparse, blob-filtered checkout of CORPUS_COMMIT; returns the project dir."""
    run = lambda *a: subprocess.run(["git", "-C", str(dest), *a], check=True)  # noqa: E731
    dest.mkdir(parents=True, exist_ok=True)
    run("init", "--quiet")
    run("remote", "add", "origin", CORPUS_REPO)
    run("sparse-checkout", "set", "--no-cone", *TEXT_PATTERNS)
    print(f"fetching {CORPUS_REPO} @{CORPUS_COMMIT[:12]} (text files only) into {dest} ...")
    run("fetch", "--quiet", "--depth", "1", "--filter=blob:none", "origin", CORPUS_COMMIT)
    run("checkout", "--quiet", "FETCH_HEAD")
    return dest / PROJECT_SUBDIR


CALL_RE = re.compile(
    r"- m_Target: \{fileID: (-?\d+)(?:, guid: ([0-9a-f]{32}))?[^}\n]*\}\s*\n"
    r"(?:[ \t]*m_TargetAssemblyTypeName:[ \t]*([^\n]*)\n)?[ \t]*m_MethodName:[ \t]*(\w*)")
DOC_SCRIPT_RE = re.compile(
    r"^--- !u!114 &(-?\d+).*?^  m_Script: \{fileID: 11500000, guid: ([0-9a-f]{32})",
    re.M | re.S)


def build_truth(project: Path) -> dict:
    assets_dir = project / "Assets"
    scripts: dict[str, str] = {}            # guid -> Assets-relative .cs path
    for meta in assets_dir.rglob("*.cs.meta"):
        m = GUID_RE.search(meta.read_text(encoding="utf-8", errors="replace"))
        if m:
            scripts[m.group(1)] = meta.relative_to(assets_dir).as_posix()[:-5]
    stems = {Path(p).stem for p in scripts.values()}
    prefabs: dict[str, str] = {}            # guid -> Assets-relative .prefab path
    for meta in assets_dir.rglob("*.prefab.meta"):
        m = GUID_RE.search(meta.read_text(encoding="utf-8", errors="replace"))
        if m:
            prefabs[m.group(1)] = meta.relative_to(assets_dir).as_posix()[:-5]
    instances: dict[str, set[str]] = defaultdict(set)  # asset -> prefabs it instances
    code = "\n".join((assets_dir / p).read_text(encoding="utf-8", errors="replace")
                     for p in scripts.values())
    users: dict[str, set[str]] = defaultdict(set)      # script path -> asset files
    # UnityEvent calls by category: (owner, method) -> files
    calls: dict[str, dict[tuple[str, str], set[str]]] = {
        "project": defaultdict(set), "stale": defaultdict(set), "no_target": defaultdict(set),
        "engine": defaultdict(set), "unresolved": defaultdict(set)}
    anim_calls: dict[str, set[str]] = defaultdict(set)
    override_calls: dict[str, set[str]] = defaultdict(set)   # method -> files
    asset_files = 0
    by_ext = Counter()
    declared: dict[str, bool] = {}

    def is_declared(method: str) -> bool:
        if method not in declared:
            declared[method] = bool(re.search(rf"[\w>\]]\s+{re.escape(method)}\s*[({{=;]", code))
        return declared[method]

    for path in assets_dir.rglob("*"):
        if path.suffix == ".meta" and not path.name.endswith(".cs.meta") and path.is_file():
            # Model import settings carry animation events of their clips.
            text = path.read_text(encoding="utf-8", errors="replace")
            for fn in set(ANIM_EVENT_RE.findall(text)):
                anim_calls[fn].add(path.relative_to(assets_dir).as_posix())
            continue
        if path.suffix not in ASSET_EXTS or not path.is_file():
            continue
        asset_files += 1
        by_ext[path.suffix] += 1
        rel = path.relative_to(assets_dir).as_posix()
        text = path.read_text(encoding="utf-8", errors="replace")
        for guid in set(SCRIPT_RE.findall(text)):
            if guid in scripts:
                users[scripts[guid]].add(rel)
        for guid in set(SOURCE_PREFAB_RE.findall(text)):
            if guid in prefabs and prefabs[guid] != rel:
                instances[rel].add(prefabs[guid])
        if "m_PersistentCalls" in text:
            local = {fid: guid for fid, guid in DOC_SCRIPT_RE.findall(text)}
            for fid, guid, type_name, method in CALL_RE.findall(text):
                if not method:
                    continue
                method = method[4:] if method.startswith(("set_", "get_")) else method
                owner = ""
                if fid in local and local[fid] in scripts:
                    owner = Path(scripts[local[fid]]).stem
                elif type_name.strip():
                    owner = type_name.split(",")[0].strip().replace("+", ".").rsplit(".", 1)[-1]
                if fid == "0" and not guid:
                    kind = "no_target"
                elif fid in local and local[fid] not in scripts:
                    kind = "unresolved"  # the component's script is a package's or gone
                elif owner not in stems:
                    kind = "engine"      # UnityEngine / package type, or an asset target
                else:
                    kind = "project" if is_declared(method) else "stale"
                calls[kind][(owner, method)].add(rel)
            for method in OVERRIDE_CALL_RE.findall(text):
                method = method[4:] if method.startswith(("set_", "get_")) else method
                if is_declared(method):
                    override_calls[method].add(rel)
        for fn in set(ANIM_EVENT_RE.findall(text)):
            anim_calls[fn].add(rel)
    return {"scripts": scripts, "users": users, "calls": calls,
            "anim_calls": anim_calls, "override_calls": override_calls,
            "instances": instances, "asset_files": asset_files, "by_ext": by_ext}


def evaluate(project: Path, store: Store, truth: dict) -> dict:
    """What `refs` / `unity missing` report, through the same functions."""
    users, calls = truth["users"], truth["calls"]
    uses_cache: dict[str, object] = {}

    def uses(name: str):
        if name not in uses_cache:
            uses_cache[name] = refsview.asset_uses(store, [name], store.symbols_named(name))
        return uses_cache[name]

    shown = total = scripts_hit = extra = 0
    code_unused_but_used, x1_now_visible = [], 0
    for script, files in sorted(users.items()):
        main = Path(script).stem
        u = uses(main)
        got = {x.path for x in u.scripts} if u else set()
        shown += len(files & got)
        extra += len(got - files)
        total += len(files)
        scripts_hit += bool(files & got)
        code_sites = {p for p, _, _ in store.reference_sites(main)
                      if p.endswith(".cs") and p != script}
        if not code_sites:
            code_unused_but_used.append((script, len(files)))
            x1_now_visible += bool(files & got)
    project_calls = calls["project"]
    pc_hit = sum(bool(files & {x.path for x in (uses(m).calls if uses(m) else [])})
                 for (_, m), files in project_calls.items())
    anim_hit = sum(bool(f & {x.path for x in (uses(fn).anims if uses(fn) else [])})
                   for fn, f in truth["anim_calls"].items() if store.symbols_named(fn))
    anim_defined = sum(1 for fn in truth["anim_calls"] if store.symbols_named(fn))
    ov_hit = sum(bool(f & {x.path for x in (uses(m).calls if uses(m) else [])})
                 for m, f in truth["override_calls"].items())
    # X1: `unused` must not list what the assets use.
    rep = unused.collect_unused(store, project / "Assets")
    used_scripts = {Path(s).stem for s in users}
    used_methods = ({m for (_, m) in calls["project"]} | {m for (_, m) in calls["stale"]}
                    | set(truth["override_calls"]) | set(truth["anim_calls"]))
    unused_but_data = [c.row.name for c in rep.candidates
                       if c.row.name in (used_scripts if c.row.kind in TYPE_KINDS
                                         else used_methods)]
    trap_listed = sum(1 for c in rep.candidates
                      if c.row.kind in TYPE_KINDS and c.row.name in
                      {Path(s).stem for s, _ in code_unused_but_used})
    # X2: `why asset script` must follow the same links (sampled; one graph).
    graph = why.Graph(store, project / "Assets")
    times: list[float] = []

    def route(a: str, b: str) -> list:
        t0 = time.perf_counter()
        res = why.find_path(store, a, b, graph=graph)
        times.append(time.perf_counter() - t0)
        return [] if res.reverse else res.hops

    links = sorted((a, s) for s, files in users.items() for a in files)
    direct = links[::max(1, len(links) // 100)]
    direct_hit = sum([h.kind for h in route(a, s)] == [why.SCRIPT] for a, s in direct)
    scripts_of: dict[str, set[str]] = defaultdict(set)
    for s, files in users.items():
        for a in files:
            scripts_of[a].add(s)
    nested_all = sorted({(a, s) for a, ps in truth["instances"].items() for p in ps
                         for s in scripts_of.get(p, ()) if a not in users[s]})
    nested = nested_all[::max(1, len(nested_all) // 100)]
    nested_hit = sum(0 < len(route(a, s)) <= 2 for a, s in nested)
    def first_hop_calls(a: str, b: str) -> set[str]:
        hops = route(a, b)
        return hops[0].calls if hops else set()

    call_hit = sum(m in first_hop_calls(sorted(files)[0], f"{owner}.{m}")
                   for (owner, m), files in sorted(project_calls.items()))
    missing = unityassets.render_missing(store, project / "Assets")
    stale_hit = sum(any(f"{f}:" in line and line.endswith(f".{m}")
                        for f in files for line in missing.splitlines())
                    for (_, m), files in calls["stale"].items())
    nt_hit = sum(any(f"{f}:" in line and line.endswith(m)
                     for f in files for line in missing.splitlines())
                 for (_, m), files in calls["no_target"].items())
    return {"asset_user_links": total, "asset_user_links_shown": shown,
            "asset_user_links_extra": extra,
            "scripts_with_users": len(users), "scripts_with_users_shown": scripts_hit,
            "project_calls": len(project_calls), "project_calls_shown": pc_hit,
            "stale_calls": len(calls["stale"]), "stale_calls_shown": stale_hit,
            "no_target_calls": len(calls["no_target"]), "no_target_calls_shown": nt_hit,
            "engine_calls": len(calls["engine"]),
            "unresolved_calls": len(calls["unresolved"]),
            "anim_methods": anim_defined, "anim_methods_shown": anim_hit,
            "anim_orphans": len(truth["anim_calls"]) - anim_defined,
            "override_calls": len(truth["override_calls"]), "override_calls_shown": ov_hit,
            "unused_candidates": len(rep.candidates),
            "unused_types": sum(1 for c in rep.candidates if c.row.kind in TYPE_KINDS),
            "unused_but_data": unused_but_data, "unused_trap_listed": trap_listed,
            "why_direct": len(direct), "why_direct_hit": direct_hit,
            "why_nested": len(nested), "why_nested_hit": nested_hit,
            "why_calls_hit": call_hit,
            "why_ms": round(1000 * sorted(times)[len(times) // 2], 1) if times else 0,
            "code_unused_but_used": code_unused_but_used, "x1_now_visible": x1_now_visible}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--project", help="existing Chop Chop checkout (UOP1_Project dir)")
    ap.add_argument("--keep", action="store_true", help="keep the temp checkout")
    ap.add_argument("--db", help="scratch dir for the index (default: temp)")
    ap.add_argument("--json", help="write machine-readable results here")
    args = ap.parse_args()

    tmp = None
    if args.project:
        project = Path(args.project).resolve()
    else:
        tmp = Path(tempfile.mkdtemp(prefix="lotsman_bench_chopchop_"))
        t0 = time.monotonic()
        project = fetch(tmp / "repo")
        print(f"fetched in {time.monotonic() - t0:.0f}s")
    try:
        root = project / "Assets"
        db_dir = Path(args.db) if args.db else Path(tempfile.mkdtemp(prefix="lotsman_game_"))
        store = Store(db_dir / "index.db")
        t0 = time.monotonic()
        res = indexer.index_repo(root, store)
        index_s = time.monotonic() - t0
        t0 = time.monotonic()
        truth = build_truth(project)
        truth_s = time.monotonic() - t0
        ev = evaluate(project, store, truth)
        n_types = store.conn.execute(
            "SELECT count(*) FROM symbols WHERE kind IN (%s)" % ",".join("?" * len(TYPE_KINDS)),
            TYPE_KINDS).fetchone()[0]
        store.close()

        print(f"\nlotsman benchmark - Chop Chop @{CORPUS_COMMIT[:12]} (Unity 2020.3)")
        print(f"index: +{res.added} files, {n_types} types, {index_s:.1f}s (db {db_dir})")
        exts = ", ".join(f"{k} {v}" for k, v in truth["by_ext"].most_common())
        print(f"assets: {truth['asset_files']} files ({exts}); truth built in {truth_s:.1f}s")
        print(f"scripts with a .meta GUID: {len(truth['scripts'])}\n")
        pct = lambda a, b: f"{100 * a / b:.0f}%" if b else "-"  # noqa: E731
        rows = [
            ("scripts used by assets (m_Script)", ev["scripts_with_users"],
             ev["scripts_with_users_shown"], "refs"),
            ("script → asset links", ev["asset_user_links"], ev["asset_user_links_shown"],
             "refs"),
            ("UnityEvent calls to project methods", ev["project_calls"],
             ev["project_calls_shown"], "refs"),
            ("UnityEvent calls set by prefab overrides", ev["override_calls"],
             ev["override_calls_shown"], "refs"),
            ("animation-event methods (defined)", ev["anim_methods"],
             ev["anim_methods_shown"], "refs"),
            ("stale UnityEvent calls (method gone)", ev["stale_calls"],
             ev["stale_calls_shown"], "unity missing"),
            ("UnityEvent calls without a target", ev["no_target_calls"],
             ev["no_target_calls_shown"], "unity missing"),
        ]
        print(f"{'serialized reference':40} {'truth':>6} {'shown':>6} {'%':>5}  by")
        for label, t, s_, by in rows:
            print(f"{label:40} {t:6} {s_:6} {pct(s_, t):>5}  {by}")
        print(f"(script links lotsman lists that the truth lacks: "
              f"{ev['asset_user_links_extra']}; UnityEvent calls into Unity/package types: "
              f"{ev['engine_calls']}, on components whose script is a package's or missing "
              f"(listed by `unity missing` as unresolved scripts): {ev['unresolved_calls']}; "
              f"animation events naming no project "
              f"method: {ev['anim_orphans']})")
        trap = ev["code_unused_but_used"]
        print(f"\nX1 trap: {len(trap)} scripts used by assets for which `refs` finds no "
              f"code use outside their own file; `refs` lists asset users for "
              f"{ev['x1_now_visible']} of them, e.g. "
              + ", ".join(f"{Path(s).stem} ({n})" for s, n in sorted(trap, key=lambda x: -x[1])[:5]))
        print(f"\nX1 `unused`: {ev['unused_candidates']} candidates "
              f"({ev['unused_types']} types); used by assets yet listed: "
              f"{len(ev['unused_but_data'])} {ev['unused_but_data'][:5]}; X1-trap scripts "
              f"listed: {ev['unused_trap_listed']} of {len(trap)}")
        print(f"X2 `why`: asset -> its script in 1 hop {ev['why_direct_hit']}/"
              f"{ev['why_direct']}; -> a script of an instanced prefab in <= 2 hops "
              f"{ev['why_nested_hit']}/{ev['why_nested']}; UnityEvent calls reached by a "
              f"call hop {ev['why_calls_hit']}/{ev['project_calls']}; median "
              f"{ev['why_ms']} ms/query")
        gates = [
            ("scripts", len(truth["scripts"]) >= GATES["min_scripts"]),
            ("asset files", truth["asset_files"] >= GATES["min_asset_files"]),
            ("scripts used by assets", ev["scripts_with_users"] >= GATES["min_script_users"]),
            ("UnityEvent calls", ev["project_calls"] + ev["stale_calls"]
             >= GATES["min_data_methods"]),
            ("script links shown ≥ 95%", ev["asset_user_links_shown"]
             >= 0.95 * ev["asset_user_links"]),
            ("project UnityEvent calls shown ≥ 90%", ev["project_calls_shown"]
             >= 0.9 * ev["project_calls"]),
            ("unused lists nothing the assets use", not ev["unused_but_data"]),
            ("why: asset -> script 100%", ev["why_direct_hit"] == ev["why_direct"]),
            ("why: through prefab instances ≥ 95%", ev["why_nested_hit"]
             >= 0.95 * ev["why_nested"]),
            ("why: UnityEvent calls ≥ 90%", ev["why_calls_hit"] >= 0.9 * ev["project_calls"]),
        ]
        failed = [name for name, ok in gates if not ok]
        print("\ncorpus gates: " + ("pass" if not failed else "FAIL: " + ", ".join(failed)))
        if args.json:
            out = {k: v for k, v in ev.items() if k != "code_unused_but_used"}
            out.update(commit=CORPUS_COMMIT, index_s=round(index_s, 2),
                       asset_files=truth["asset_files"], scripts=len(truth["scripts"]),
                       code_unused_but_used=len(trap))
            Path(args.json).write_text(json.dumps(out, indent=2))
        return 1 if failed else 0
    finally:
        if tmp and not args.keep:
            shutil.rmtree(tmp, ignore_errors=True)
        elif tmp:
            print(f"kept checkout: {project}")


if __name__ == "__main__":
    sys.exit(main())
