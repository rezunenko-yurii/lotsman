#!/usr/bin/env python3
"""Index snapshot (X3): a missing index restored from a snapshot vs built cold.

For a repo (and optionally an older tree of it, as when the snapshot lags
the checkout): build a cold index of the snapshot's tree with embeddings,
export it, restore it into a fresh index path for the repo (what a new
clone, worktree or CI runner does), refresh, embed what changed, then build
the repo's index cold and compare every table row by row (paths instead of
ids, mtimes excluded, vectors byte for byte).

Usage:
    python benchmarks/bench_snapshot.py --repo PATH [--from OLDER_TREE] [--out DIR]

Gate: the restored index equals the cold one.
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lotsman import embed, indexer, snapshot  # noqa: E402
from lotsman.store import Store  # noqa: E402

ROWS = {
    "files": "SELECT path, sha, size, lang FROM files",
    "symbols": "SELECT f.path, s.name, s.kind, s.line, s.end_line, s.signature, s.arity, "
               "s.vector FROM symbols s JOIN files f ON f.id = s.file_id",
    "idents": "SELECT f.path, i.name, i.count, i.lines FROM idents i JOIN files f ON f.id = i.file_id",
    "bases": "SELECT f.path, b.type_name, b.base_name, b.line FROM bases b "
             "JOIN files f ON f.id = b.file_id",
    "assemblies": "SELECT f.path, a.kind, a.name, a.data FROM assemblies a "
                  "JOIN files f ON f.id = a.file_id",
    "directives": "SELECT f.path, d.kind, d.line, d.end_line, d.cond FROM directives d "
                  "JOIN files f ON f.id = d.file_id",
    "asset_refs": "SELECT f.path, a.kind, a.key, a.owner, a.line, a.count FROM asset_refs a "
                  "JOIN files f ON f.id = a.file_id",
    "guids": "SELECT f.path, g.guid FROM guids g JOIN files f ON f.id = g.file_id",
    "sig_changes": "SELECT path, name FROM sig_changes",
    "meta": "SELECT key, value FROM meta WHERE key != 'rank_cache_stamp'",
}


def cold(root: Path, db: Path) -> tuple[Store, float]:
    t0 = time.monotonic()
    store = Store(db)
    indexer.index_repo(root, store)
    embed.embed_missing(store)
    return store, time.monotonic() - t0


def differences(a: Store, b: Store) -> dict[str, int]:
    tables = dict(ROWS)
    if a.has_fts and b.has_fts:
        tables["docs_fts"] = "SELECT heading, body FROM docs_fts"
    out = {}
    for name, q in tables.items():
        ra = sorted(map(repr, a.conn.execute(q)))
        rb = sorted(map(repr, b.conn.execute(q)))
        if ra != rb:
            out[name] = len(set(ra) ^ set(rb))
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--repo", required=True, help="the checkout the index is for")
    ap.add_argument("--from", dest="older", help="older tree the snapshot is built from")
    ap.add_argument("--out", help="scratch dir (default: temp, removed)")
    args = ap.parse_args()
    root = Path(args.repo).resolve()
    src = Path(args.older).resolve() if args.older else root
    out = Path(args.out) if args.out else Path(tempfile.mkdtemp(prefix="lotsman_snap_"))
    shutil.rmtree(out, ignore_errors=True)
    out.mkdir(parents=True)
    embed.available()  # load the model outside the timings
    try:
        store, t_src = cold(src, out / "src.db")
        exp = indexer.export_snapshot(store, out / "index.snapshot")
        store.close()

        os.environ[snapshot.SNAPSHOT_ENV] = str(out / "index.snapshot")
        os.environ[indexer.INDEX_ENV] = str(out / "restored.db")
        t0 = time.monotonic()
        restored = indexer.open_store(root)
        t_restore = time.monotonic() - t0
        res = indexer.index_repo(root, restored)
        embedded = embed.embed_missing(restored)
        t_total = time.monotonic() - t0

        ref, t_cold = cold(root, out / "cold.db")
        diff = differences(restored, ref)
        restored.close()
        ref.close()
    finally:
        if not args.out:
            shutil.rmtree(out, ignore_errors=True)

    note = restored.restored.note if restored.restored else "no snapshot found"
    print(f"lotsman benchmark - index snapshot: {root}"
          + (f" (snapshot of {src})" if src != root else ""))
    print(f"cold index + embeddings    {t_cold:6.2f}s")
    print(f"export                     {exp['seconds']:6.2f}s  {exp['bytes'] / 1e6:.1f} MB "
          f"(index {exp['db_bytes'] / 1e6:.1f} MB, {exp['files']} files)")
    print(f"restore                    {t_restore:6.2f}s  {note or 'ok'}")
    print(f"refresh + embed            {t_total - t_restore:6.2f}s  +{res.added} ~{res.updated} "
          f"-{res.removed} ={res.unchanged}, embedded {embedded}")
    print(f"restored total             {t_total:6.2f}s  ({t_cold / t_total:.1f}x faster)")
    print(f"differs from cold          {diff or 'nothing'}")
    ok = not note and not diff
    print(f"gate (restored index == cold index): {'pass' if ok else 'FAIL'}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
