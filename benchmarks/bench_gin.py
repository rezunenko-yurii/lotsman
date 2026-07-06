#!/usr/bin/env python3
"""Reproducible benchmark: lotsman on the Gin Go web framework.

Measures indexing (cold / no-op / single-file change), map (cold / warm),
search latency, and token cost for a realistic Go navigation scenario versus
reading whole files.

Usage:
    python benchmarks/bench_gin.py [--gin-dir PATH] [--keep]

Without --gin-dir the script shallow-clones Gin at a pinned tag into a temp
directory (network required) and removes it afterwards unless --keep.
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

GIN_REPO = "https://github.com/gin-gonic/gin.git"
GIN_TAG = "v1.10.0"

MAP_MUST_CONTAIN = ["gin.go", "context.go", "routergroup.go", "binding/binding.go"]
DEF_MUST_DEFINE = [
    ("Recovery", "recovery.go"),
    ("JSON", "context.go"),
]
NAV_QUESTIONS = [
    ("route group middleware handlers", "routergroup.go", 5),
    ("bind json request body", "binding/json.go", 5),
    ("serve http request engine", "gin.go", 5),
]


def timed(fn, *args, **kwargs):
    t0 = time.monotonic()
    result = fn(*args, **kwargs)
    return result, time.monotonic() - t0


def get_gin(path: str | None) -> tuple[Path, bool]:
    if path:
        return Path(path).resolve(), False
    tmp = Path(tempfile.mkdtemp(prefix="lotsman_bench_gin_"))
    print(f"cloning gin @{GIN_TAG} (shallow) into {tmp} ...")
    subprocess.run(
        ["git", "clone", "--depth", "1", "--branch", GIN_TAG, "--quiet",
         GIN_REPO, str(tmp / "gin")],
        check=True)
    return tmp / "gin", True


def scenario_tokens(root: Path, store) -> tuple[int, int]:
    """Agent scenario: locate Gin's Context.JSON response path.

    lotsman path: search -> defs -> outline -> read only the method slice.
    naive path:   read the likely whole files an agent would inspect.
    """
    tool_output = ""
    hits = search.search(store, "context json response", limit=5)
    tool_output += "\n".join(
        f"{h.symbol.path}:{h.symbol.line} {h.symbol.signature}" for h in hits)
    defs = store.symbols_named("JSON")
    tool_output += "\n".join(f"{d.path}:{d.line} {d.signature}" for d in defs)
    outline = store.symbols_in_file("context.go")
    tool_output += "\n".join(
        f"{r.line}-{r.end_line} {r.signature}" for r in outline)

    context = (root / "context.go").read_text(errors="replace")
    target = next(d for d in defs if d.path == "context.go")
    slice_text = "\n".join(
        context.splitlines()[max(target.line - 8, 0):target.line + 40])
    smart = estimate_tokens(tool_output) + estimate_tokens(slice_text)

    naive = sum(estimate_tokens((root / p).read_text(errors="replace")) for p in (
        "context.go",
        "render/json.go",
        "gin.go"))
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
    ap.add_argument("--gin-dir", help="existing Gin checkout (skips clone)")
    ap.add_argument("--keep", action="store_true", help="keep the temp clone")
    args = ap.parse_args()

    root, is_temp = get_gin(args.gin_dir)
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

        target = root / "context.go"
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

        _, t = timed(search.search, store, "context json response", 10)
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
    print(f"\nlotsman benchmark - gin @{GIN_TAG}")
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
