"""CLI: lotsman index | map | search | outline | defs | refs | stats."""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import asdict
from pathlib import Path

from lotsman import embed, indexer, repomap, search as search_mod
from lotsman.store import Store


# Confidence contract for name-based lookups, embedded in JSON outputs.
REFS_CONFIDENCE = {
    "level": "heuristic",
    "resolution": "name-based",
    "type_resolution": False,
    "known_blind_spots": ["reflection", "dependency_injection",
                          "generated_code", "serialized_assets"],
}


def _root(args) -> Path:
    root = Path(args.repo).resolve()
    if not root.is_dir():
        sys.exit(f"error: not a directory: {root}")
    return root


def _open(root: Path, auto_index: bool = True) -> Store:
    """Open the index, keeping read commands fresh before serving results."""
    db = root / indexer.DB_RELPATH
    if not db.exists():
        if not auto_index:
            sys.exit("error: no index found — run `lotsman index` first")
        store = Store(db)
        res = indexer.index_repo(root, store)
        print(f"[lotsman] first-time index: {res.added} files in {res.seconds:.1f}s",
              file=sys.stderr)
        return store
    store = Store(db)
    res = indexer.index_repo(root, store)
    if res.added or res.updated or res.removed:
        print(f"[lotsman] index refreshed: +{res.added} ~{res.updated} "
              f"-{res.removed}", file=sys.stderr)
    return store


def cmd_index(args) -> int:
    root = _root(args)
    store = indexer.open_store(root)
    res = indexer.index_repo(root, store, verify=args.verify)
    embedded = 0
    if not args.no_embed:
        t0 = time.monotonic()
        embedded = embed.embed_missing(store)
        if embedded:
            print(f"[lotsman] embedded {embedded} symbols in "
                  f"{time.monotonic() - t0:.1f}s", file=sys.stderr)
        elif not embed.available():
            print("[lotsman] embeddings unavailable (pip install model2vec) — "
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
    print(f"[lotsman] map in {time.monotonic() - t0:.2f}s", file=sys.stderr)
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
    print(f"[lotsman] search in {time.monotonic() - t0:.2f}s", file=sys.stderr)
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
            # JSON must carry the same honesty as the text output — agents
            # binding to this shape must see the confidence level.
            "confidence": REFS_CONFIDENCE,
        }, ensure_ascii=False))
        return 0
    if defs:
        print("defined in:")
        for d in defs:
            print(f"  {d.path}:{d.line}  [{d.kind}] {d.signature}")
    if ref_only:
        print("referenced by (name-based matching, no type resolution):")
        for p, c in ref_only[:args.limit]:
            print(f"  {p}  ({c}x)")
    if not defs and not ref_only:
        print(f"(`{args.name}` not found in index)")
        return 1
    return 0


def cmd_impact(args) -> int:
    from lotsman import impact
    root = _root(args)
    store = _open(root)
    indexer.index_repo(root, store)  # impact must see the current disk state
    if args.files:
        changed, method = list(args.files), "explicit"
    else:
        changed, method = impact.detect_changed(root, store, args.since)
    out = impact.generate_impact(store, changed, budget=args.budget)
    store.close()
    print(out, end="")
    print(f"[lotsman] impact via {method}", file=sys.stderr)
    return 0


def cmd_init(args) -> int:
    from lotsman.init_cmd import run_init
    return run_init(_root(args), agents=args.agent or [],
                    no_index=args.no_index)


def cmd_doctor(args) -> int:
    from lotsman.doctor import run_doctor
    return run_doctor(_root(args), as_json=args.json,
                      fail_on_warn=args.fail_on_warn)


def cmd_mcp(args) -> int:
    from lotsman.mcp_server import serve
    return serve(_root(args))


def cmd_stats(args) -> int:
    root = _root(args)
    store = _open(root, auto_index=False)
    stats = store.stats()
    store.close()
    print(json.dumps(stats, indent=2, ensure_ascii=False))
    return 0


def main(argv: list[str] | None = None) -> int:
    from lotsman import __version__
    p = argparse.ArgumentParser(
        prog="lotsman",
        description="Local codebase index for AI agents: repo map, symbol "
                    "search, reference graph.")
    p.add_argument("--version", action="version",
                   version=f"lotsman {__version__}")
    p.add_argument("--repo", default=".", help="repository root (default: cwd)")
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("init", help="one-command onboarding: policy in "
                                     "AGENTS.md, ignore skeleton, agent "
                                     "configs, first index")
    sp.add_argument("--agent", action="append",
                    choices=["claude", "codex", "cursor"],
                    help="also write agent-specific config (repeatable)")
    sp.add_argument("--no-index", action="store_true",
                    help="skip the initial index/warm-up")
    sp.set_defaults(fn=cmd_init)

    sp = sub.add_parser("index", help="build/update the index incrementally")
    sp.add_argument("--json", action="store_true")
    sp.add_argument("--no-embed", action="store_true",
                    help="skip embedding pass (search degrades to BM25)")
    sp.add_argument("--verify", action="store_true",
                    help="re-hash every file, bypassing the mtime+size fast "
                         "path (catches stale-index edge cases)")
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

    sp = sub.add_parser("doctor", help="environment and index health check")
    sp.add_argument("--json", action="store_true",
                    help="machine-readable report for agents/CI")
    sp.add_argument("--fail-on-warn", action="store_true",
                    help="exit 1 on warnings, not only on failures")
    sp.set_defaults(fn=cmd_doctor)

    sp = sub.add_parser("impact", help="changed files + who depends on them")
    sp.add_argument("files", nargs="*",
                    help="repo-relative paths; empty = auto-detect (git status "
                         "or mtime window)")
    sp.add_argument("--since", type=float, default=24.0,
                    help="hours for mtime-based detection when not a git repo "
                         "(default 24)")
    sp.add_argument("--budget", type=int, default=1500)
    sp.set_defaults(fn=cmd_impact)

    sp = sub.add_parser("mcp", help="run MCP stdio server (tools: map, search, "
                                    "outline, defs, refs, impact)")
    sp.set_defaults(fn=cmd_mcp)

    args = p.parse_args(argv)
    return args.fn(args)
