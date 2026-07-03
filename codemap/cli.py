"""CLI: codemap index | map | search | outline | defs | refs | stats."""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import asdict
from pathlib import Path

from codemap import embed, indexer, repomap, search as search_mod
from codemap.store import Store


def _root(args) -> Path:
    root = Path(args.repo).resolve()
    if not root.is_dir():
        sys.exit(f"error: not a directory: {root}")
    return root


def _open(root: Path, auto_index: bool = True) -> Store:
    db = root / indexer.DB_RELPATH
    if not db.exists():
        if not auto_index:
            sys.exit("error: no index found — run `codemap index` first")
        store = Store(db)
        res = indexer.index_repo(root, store)
        print(f"[codemap] first-time index: {res.added} files in {res.seconds:.1f}s",
              file=sys.stderr)
        return store
    return Store(db)


def cmd_index(args) -> int:
    root = _root(args)
    store = indexer.open_store(root)
    res = indexer.index_repo(root, store)
    embedded = 0
    if not args.no_embed:
        t0 = time.monotonic()
        embedded = embed.embed_missing(store)
        if embedded:
            print(f"[codemap] embedded {embedded} symbols in "
                  f"{time.monotonic() - t0:.1f}s", file=sys.stderr)
        elif not embed.available():
            print("[codemap] embeddings unavailable (pip install model2vec) — "
                  "search will use BM25 only", file=sys.stderr)
    stats = store.stats()
    store.close()
    if args.json:
        print(json.dumps({**res.__dict__, **stats, "embedded": embedded},
                         ensure_ascii=False))
    else:
        print(f"scanned {res.scanned} | +{res.added} added, ~{res.updated} updated, "
              f"-{res.removed} removed, ={res.unchanged} unchanged | {res.seconds:.2f}s")
        print(f"index: {stats['symbols']} symbols, {stats['files']} files, "
              f"{stats['db_bytes'] // 1024} KiB")
        for err in res.errors[:10]:
            print(f"warn: {err}", file=sys.stderr)
    return 0


def cmd_map(args) -> int:
    root = _root(args)
    store = _open(root)
    t0 = time.monotonic()
    focus = {f.strip() for f in (args.focus or []) if f.strip()}
    mentions = {m.strip() for m in (args.mention or []) if m.strip()}
    out = repomap.generate_map(store, budget=args.budget, focus=focus,
                               mentions=mentions)
    store.close()
    print(out, end="")
    print(f"[codemap] map in {time.monotonic() - t0:.2f}s", file=sys.stderr)
    return 0


def cmd_search(args) -> int:
    root = _root(args)
    store = _open(root)
    t0 = time.monotonic()
    hits = search_mod.search(store, args.query, limit=args.limit, mode=args.mode)
    store.close()
    if args.json:
        print(json.dumps(
            [{"score": round(h.score, 3), **asdict(h.symbol)} for h in hits],
            ensure_ascii=False))
    else:
        if not hits:
            print("(no results)")
        for h in hits:
            print(f"{h.score:6.2f}  {h.symbol.path}:{h.symbol.line}  "
                  f"[{h.symbol.kind}] {h.symbol.signature}")
    print(f"[codemap] search in {time.monotonic() - t0:.2f}s", file=sys.stderr)
    return 0


def cmd_outline(args) -> int:
    root = _root(args)
    store = _open(root)
    rows = store.symbols_in_file(args.file)
    store.close()
    if args.json:
        print(json.dumps([asdict(r) for r in rows], ensure_ascii=False))
        return 0
    if not rows:
        print(f"(no symbols indexed for {args.file})")
        return 1
    print(f"{args.file}:")
    for r in rows:
        print(f"{r.line:5}-{r.end_line:<5} [{r.kind}] {r.signature}")
    return 0


def cmd_defs(args) -> int:
    root = _root(args)
    store = _open(root)
    rows = store.symbols_named(args.name)
    store.close()
    if args.json:
        print(json.dumps([asdict(r) for r in rows], ensure_ascii=False))
        return 0
    if not rows:
        print(f"(no definitions of `{args.name}`)")
        return 1
    for r in rows:
        print(f"{r.path}:{r.line}  [{r.kind}] {r.signature}")
    return 0


def cmd_refs(args) -> int:
    root = _root(args)
    store = _open(root)
    defs = store.symbols_named(args.name)
    refs = store.files_referencing(args.name)
    store.close()
    def_paths = {d.path for d in defs}
    ref_only = [(p, c) for p, c in refs if p not in def_paths]
    if args.json:
        print(json.dumps({
            "definitions": [asdict(d) for d in defs],
            "references": [{"path": p, "count": c} for p, c in ref_only],
        }, ensure_ascii=False))
        return 0
    if defs:
        print("defined in:")
        for d in defs:
            print(f"  {d.path}:{d.line}  [{d.kind}] {d.signature}")
    if ref_only:
        print("referenced by:")
        for p, c in ref_only[:args.limit]:
            print(f"  {p}  ({c}x)")
    if not defs and not ref_only:
        print(f"(`{args.name}` not found in index)")
        return 1
    return 0


def cmd_mcp(args) -> int:
    from codemap.mcp_server import serve
    return serve(_root(args))


def cmd_stats(args) -> int:
    root = _root(args)
    store = _open(root, auto_index=False)
    stats = store.stats()
    store.close()
    print(json.dumps(stats, indent=2, ensure_ascii=False))
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="codemap",
        description="Local codebase index for AI agents: repo map, symbol "
                    "search, reference graph.")
    p.add_argument("--repo", default=".", help="repository root (default: cwd)")
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("index", help="build/update the index incrementally")
    sp.add_argument("--json", action="store_true")
    sp.add_argument("--no-embed", action="store_true",
                    help="skip embedding pass (search degrades to BM25)")
    sp.set_defaults(fn=cmd_index)

    sp = sub.add_parser("map", help="token-budgeted map of the most important symbols")
    sp.add_argument("--budget", type=int, default=repomap.DEFAULT_BUDGET,
                    help="token budget (default 2048)")
    sp.add_argument("--focus", action="append", metavar="FILE",
                    help="file already in agent context; biases ranking, excluded "
                         "from output (repeatable)")
    sp.add_argument("--mention", action="append", metavar="IDENT",
                    help="identifier relevant to current task; boosts related "
                         "files (repeatable)")
    sp.set_defaults(fn=cmd_map)

    sp = sub.add_parser("search", help="hybrid BM25+vector search over symbols")
    sp.add_argument("query")
    sp.add_argument("-k", "--limit", type=int, default=10)
    sp.add_argument("--mode", choices=["auto", "hybrid", "bm25", "vector"],
                    default="auto",
                    help="auto = hybrid when vectors exist, else bm25")
    sp.add_argument("--json", action="store_true")
    sp.set_defaults(fn=cmd_search)

    sp = sub.add_parser("outline", help="symbol skeleton of one file")
    sp.add_argument("file", help="path relative to repo root")
    sp.add_argument("--json", action="store_true")
    sp.set_defaults(fn=cmd_outline)

    sp = sub.add_parser("defs", help="where a symbol is defined")
    sp.add_argument("name")
    sp.add_argument("--json", action="store_true")
    sp.set_defaults(fn=cmd_defs)

    sp = sub.add_parser("refs", help="who references a symbol")
    sp.add_argument("name")
    sp.add_argument("-k", "--limit", type=int, default=20)
    sp.add_argument("--json", action="store_true")
    sp.set_defaults(fn=cmd_refs)

    sp = sub.add_parser("stats", help="index statistics")
    sp.set_defaults(fn=cmd_stats)

    sp = sub.add_parser("mcp", help="run MCP stdio server (tools: map, search, "
                                    "outline, defs, refs)")
    sp.set_defaults(fn=cmd_mcp)

    args = p.parse_args(argv)
    return args.fn(args)
