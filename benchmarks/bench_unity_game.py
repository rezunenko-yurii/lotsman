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

from lotsman import indexer  # noqa: E402
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


def build_truth(project: Path) -> dict:
    assets_dir = project / "Assets"
    scripts: dict[str, str] = {}            # guid -> Assets-relative .cs path
    for meta in assets_dir.rglob("*.cs.meta"):
        m = GUID_RE.search(meta.read_text(encoding="utf-8", errors="replace"))
        if m:
            scripts[m.group(1)] = meta.relative_to(assets_dir).as_posix()[:-5]
    users: dict[str, set[str]] = defaultdict(set)      # script path -> asset files
    data_calls: dict[tuple[str, str], set[str]] = defaultdict(set)  # (type, method) -> files
    anim_calls: dict[str, set[str]] = defaultdict(set)
    asset_files = 0
    by_ext = Counter()
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
        if "m_PersistentCalls" in text:
            # Each call: m_Target … m_TargetAssemblyTypeName … m_MethodName.
            for block in text.split("- m_Target: ")[1:]:
                t, m = TARGET_TYPE_RE.search(block), METHOD_RE.search(block)
                if m and m.group(1):
                    owner = t.group(1).rsplit(".", 1)[-1] if t else "?"
                    data_calls[(owner, m.group(1))].add(rel)
        for fn in set(ANIM_EVENT_RE.findall(text)):
            anim_calls[fn].add(rel)
    return {"scripts": scripts, "users": users, "data_calls": data_calls,
            "anim_calls": anim_calls, "asset_files": asset_files, "by_ext": by_ext}


def lotsman_sites(store: Store, name: str) -> set[str]:
    return {p for p, _, _ in store.reference_sites(name)}


def evaluate(project: Path, store: Store, truth: dict) -> dict:
    users, data_calls = truth["users"], truth["data_calls"]
    # Script → asset users: does `refs <MainType>` list any of the asset files?
    shown = total = scripts_hit = 0
    code_unused_but_used = []
    for script, files in sorted(users.items()):
        main = Path(script).stem
        sites = lotsman_sites(store, main)
        hit = len(files & sites)
        shown += hit
        total += len(files)
        scripts_hit += bool(hit)
        code_sites = {p for p in sites if p.endswith(".cs") and p != script}
        if not code_sites:
            code_unused_but_used.append((script, len(files)))
    # Data-called methods: does `refs <Method>` list the asset that calls it?
    dm_hit = 0
    for (owner, method), files in data_calls.items():
        dm_hit += bool(files & lotsman_sites(store, method))
    anim_hit = sum(bool(f & lotsman_sites(store, fn)) for fn, f in truth["anim_calls"].items())
    return {"asset_user_links": total, "asset_user_links_shown": shown,
            "scripts_with_users": len(users), "scripts_with_users_shown": scripts_hit,
            "data_methods": len(data_calls), "data_methods_shown": dm_hit,
            "anim_methods": len(truth["anim_calls"]), "anim_methods_shown": anim_hit,
            "code_unused_but_used": code_unused_but_used}


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
             ev["scripts_with_users_shown"]),
            ("script → asset links", ev["asset_user_links"], ev["asset_user_links_shown"]),
            ("methods called by UnityEvents", ev["data_methods"], ev["data_methods_shown"]),
            ("methods called by animation events", ev["anim_methods"],
             ev["anim_methods_shown"]),
        ]
        print(f"{'serialized reference':38} {'truth':>6} {'refs':>6} {'shown':>6}")
        for label, t, s in rows:
            print(f"{label:38} {t:6} {s:6} {pct(s, t):>6}")
        unused = ev["code_unused_but_used"]
        print(f"\nX1 trap: {len(unused)} scripts used by assets for which `refs` finds no "
              f"code use outside their own file, e.g. "
              + ", ".join(f"{Path(s).stem} ({n})" for s, n in sorted(unused, key=lambda x: -x[1])[:5]))
        gates = [
            ("scripts", len(truth["scripts"]) >= GATES["min_scripts"]),
            ("asset files", truth["asset_files"] >= GATES["min_asset_files"]),
            ("scripts used by assets", ev["scripts_with_users"] >= GATES["min_script_users"]),
            ("data-called methods", ev["data_methods"] >= GATES["min_data_methods"]),
        ]
        failed = [name for name, ok in gates if not ok]
        print("\ncorpus gates: " + ("pass" if not failed else "FAIL: " + ", ".join(failed)))
        if args.json:
            out = {k: v for k, v in ev.items() if k != "code_unused_but_used"}
            out.update(commit=CORPUS_COMMIT, index_s=round(index_s, 2),
                       asset_files=truth["asset_files"], scripts=len(truth["scripts"]),
                       code_unused_but_used=len(unused))
            Path(args.json).write_text(json.dumps(out, indent=2))
        return 1 if failed else 0
    finally:
        if tmp and not args.keep:
            shutil.rmtree(tmp, ignore_errors=True)
        elif tmp:
            print(f"kept checkout: {project}")


if __name__ == "__main__":
    sys.exit(main())
