#!/usr/bin/env python3
"""Reproducible benchmark: lotsman on the ripgrep Rust workspace.

Measures indexing (cold / no-op / single-file change), map (cold / warm),
search latency, and token cost for a realistic Rust navigation scenario versus
reading whole files.

Usage:
    python benchmarks/bench_ripgrep.py [--ripgrep-dir PATH] [--keep]

Without --ripgrep-dir the script shallow-clones ripgrep at a pinned tag into a
temp directory (network required) and removes it afterwards unless --keep.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lotsman import embed, indexer, repomap, search  # noqa: E402
from lotsman.textutil import estimate_tokens  # noqa: E402

RIPGREP_REPO = "https://github.com/BurntSushi/ripgrep.git"
RIPGREP_TAG = "14.1.1"

MAP_MUST_CONTAIN = [
    "crates/core/flags/defs.rs",
    "crates/matcher/src/lib.rs",
    "crates/ignore/src/walk.rs",
    "crates/searcher/src/searcher/mod.rs",
]
DEF_MUST_DEFINE = [
    ("SearchWorker", "crates/core/search.rs"),
]
NAV_QUESTIONS = [
    ("walk directory ignore files", "crates/ignore/src/walk.rs", 5),
    ("parse command line args", "crates/core/flags/parse.rs", 5),
]


def timed(fn, *args, **kwargs):
    t0 = time.monotonic()
    result = fn(*args, **kwargs)
    return result, time.monotonic() - t0


def get_ripgrep(path: str | None) -> tuple[Path, bool]:
    if path:
        return Path(path).resolve(), False
    tmp = Path(tempfile.mkdtemp(prefix="lotsman_bench_ripgrep_"))
    print(f"cloning ripgrep @{RIPGREP_TAG} (shallow) into {tmp} ...")
    subprocess.run(
        ["git", "clone", "--depth", "1", "--branch", RIPGREP_TAG, "--quiet",
         RIPGREP_REPO, str(tmp / "ripgrep")],
        check=True)
    return tmp / "ripgrep", True


def scenario_tokens(root: Path, store) -> tuple[int, int]:
    """Agent scenario: locate ripgrep's search worker/printer flow.

    lotsman path: search -> defs -> outline -> read only the struct/search slice.
    naive path:   read likely whole files around core search, args, and printer.
    """
    tool_output = ""
    hits = search.search(store, "search worker thread printer", limit=5)
    tool_output += "\n".join(
        f"{h.symbol.path}:{h.symbol.line} {h.symbol.signature}" for h in hits)
    defs = store.symbols_named("SearchWorker")
    tool_output += "\n".join(f"{d.path}:{d.line} {d.signature}" for d in defs)
    outline = store.symbols_in_file("crates/core/search.rs")
    tool_output += "\n".join(
        f"{r.line}-{r.end_line} {r.signature}" for r in outline)

    core_search = (root / "crates/core/search.rs").read_text(errors="replace")
    target = next(d for d in defs if d.path == "crates/core/search.rs")
    slice_text = "\n".join(
        core_search.splitlines()[max(target.line - 8, 0):target.line + 120])
    smart = estimate_tokens(tool_output) + estimate_tokens(slice_text)

    naive = sum(estimate_tokens((root / p).read_text(errors="replace")) for p in (
        "crates/core/search.rs",
        "crates/core/flags/hiargs.rs",
        "crates/printer/src/standard.rs"))
    return smart, naive


def quality_checks(store) -> list[tuple[str, bool]]:
    checks: list[tuple[str, bool]] = []
    map_text = repomap.generate_map(store, budget=2048)
    for item in MAP_MUST_CONTAIN:
        checks.append((f"map contains {item}", item in map_text))
    for name, expected in DEF_MUST_DEFINE:
        found = any(expected == sym.path for sym in store.symbols_named(name))
        checks.append((f"defs {name} -> {expected}", found))
    for query, expected, k in NAV_QUESTIONS:
        hits = search.search(store, query, limit=k)
        found = any(expected == h.symbol.path for h in hits)
        checks.append((f"search '{query}' -> {expected} in top-{k}", found))
    return checks


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ripgrep-dir", help="existing ripgrep checkout (skips clone)")
    ap.add_argument("--keep", action="store_true", help="keep the temp clone")
    args = ap.parse_args()

    root, is_temp = get_ripgrep(args.ripgrep_dir)
    rows: list[tuple[str, str]] = []
    try:
        shutil.rmtree(root / ".lotsman", ignore_errors=True)
        store = indexer.open_store(root)

        res, t = timed(indexer.index_repo, root, store)
        rows.append(("cold index", f"{t:.2f}s ({res.scanned} files)"))
        n, t = timed(embed.embed_missing, store)
        rows.append(("embedding pass",
                     f"{t:.2f}s ({n} symbols)" if n else "skipped - no model2vec"))
        res, t = timed(indexer.index_repo, root, store)
        rows.append(("no-op reindex", f"{t:.2f}s"))

        target = root / "crates/core/search.rs"
        original_target = target.read_text(errors="replace")
        try:
            target.write_text(original_target + "\n// bench touch\n")
            res, t = timed(indexer.index_repo, root, store)
        finally:
            target.write_text(original_target)
        rows.append(("reindex after 1-file edit", f"{t:.2f}s"))

        _, t = timed(repomap.generate_map, store, 2048)
        rows.append(("map (cold, computes ranks)", f"{t:.2f}s"))
        _, t = timed(repomap.generate_map, store, 2048)
        rows.append(("map (warm, rank cache)", f"{t:.2f}s"))

        _, t = timed(search.search, store, "search worker thread printer", 10)
        rows.append(("search (bm25/hybrid auto)", f"{t:.2f}s"))

        smart, naive = scenario_tokens(root, store)
        rows.append(("scenario: lotsman tokens", f"~{smart:,}"))
        rows.append(("scenario: whole-files tokens", f"~{naive:,}"))
        rows.append(("scenario: savings", f"{naive / smart:.0f}x"))

        quality = quality_checks(store)
        store.close()
    finally:
        if is_temp and not args.keep:
            shutil.rmtree(root.parent, ignore_errors=True)

    width = max(len(name) for name, _ in rows)
    print(f"\nlotsman benchmark - ripgrep @{RIPGREP_TAG}")
    print("-" * (width + 20))
    for name, value in rows:
        print(f"{name:<{width}}  {value}")

    print("\nquality gates")
    if not embed.available():
        print("warning: model2vec unavailable - gates are calibrated for "
              "hybrid search and may misreport in BM25-only mode")
    print("-" * (width + 20))
    failed = 0
    for name, ok in quality:
        print(f"{'PASS' if ok else 'FAIL'}  {name}")
        failed += 0 if ok else 1
    if failed:
        print(f"\n{failed} quality gate(s) FAILED - ranking/search regression")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
