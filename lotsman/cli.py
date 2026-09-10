"""CLI: lotsman index | map | search | outline | defs | refs | stats."""

from __future__ import annotations

import argparse
import io
import json
import sys
import time
from dataclasses import asdict
from pathlib import Path

from lotsman import (embed, extract, indexer, outlineview, querylog, refsview,
                     repomap, search as search_mod, sliceview)
from lotsman.store import Store
from lotsman.textutil import is_test_path


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
    db = indexer.db_path(root)
    if not db.exists():
        if not auto_index:
            sys.exit("error: no index found — run `lotsman index` first")
        store = Store(db)
        res = indexer.index_repo(root, store)
        print(f"[lotsman] first-time index: {res.added} files in {res.seconds:.1f}s",
              file=sys.stderr)
        _warn_degraded(res)
        return store
    store = Store(db)
    res = indexer.index_repo(root, store)
    if res.added or res.updated or res.removed:
        print(f"[lotsman] index refreshed: +{res.added} ~{res.updated} "
              f"-{res.removed}", file=sys.stderr)
    _warn_degraded(res)
    return store


def _warn_degraded(res) -> None:
    if res.degraded:
        print(f"[lotsman] {extract.degraded_warning(res.degraded)}",
              file=sys.stderr)


def cmd_index(args) -> int:
    root = _root(args)
    store = indexer.open_store(root)
    res = indexer.index_repo(root, store, verify=args.verify)
    _warn_degraded(res)
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
    doc_hits = search_mod.search_docs(store, args.query, limit=args.docs, root=root)
    binding = store.binding_docs()
    store.close()
    if args.json:
        # Doc sections come after code hits in the same list, shaped like
        # symbols (kind "section") plus a snippet.
        print(json.dumps(
            [{"score": round(h.score, 3), **asdict(h.symbol)} for h in hits]
            + [{"score": round(d.score, 3), "path": d.path, "name": d.signature.lstrip("# "),
                "kind": "section", "line": d.line, "end_line": d.end_line,
                "signature": d.signature, "snippet": d.snippet,
                "binding": d.path in binding} for d in doc_hits],
            ensure_ascii=False))
    else:
        if not hits and not doc_hits:
            print("(no results)")
        for h in hits:
            print(f"{h.score:6.2f}  {h.symbol.path}:{h.symbol.line}  "
                  f"[{h.symbol.kind}] {h.symbol.signature}")
        for line in search_mod.render_doc_hits(doc_hits, binding):
            print(line)
    print(f"[lotsman] search in {time.monotonic() - t0:.2f}s", file=sys.stderr)
    return 0


def cmd_outline(args) -> int:
    root = _root(args)
    store = _open(root)
    rows = store.symbols_in_file(args.file)
    header = outlineview.outline_header(store, args.file, rows) if rows else []
    marks = outlineview.condition_marks(store, args.file, rows)
    store.close()
    if args.json:
        print(json.dumps([asdict(r) for r in rows], ensure_ascii=False))
        return 0
    if not rows:
        print(f"(no symbols indexed for {args.file})")
        return 1
    print(f"{args.file}:")
    print("\n".join([*header, *outlineview.outline_rows(rows, marks=marks)]))
    return 0


def cmd_slice(args) -> int:
    root = _root(args)
    store = _open(root)
    print(sliceview.generate_slice(store, root, args.file, args.name))
    store.close()
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
    if args.json:
        result = refsview.collect_refs(store, args.name)
        parts = [part for part in args.name.split(".") if part]
        refs = result.refs if result else []
        payload = {
            "definitions": [asdict(d) for d in result.defs] if result else [],
            "references": [
                {"path": s.path, "count": s.count, "lines": s.lines,
                 "data": refsview.is_data_path(s.path)}
                | ({"same_line": s.same_line} if len(parts) >= 2 else {})
                for s in refs],
            # Name shared by definitions in several files: references may
            # belong to any of them.
            "ambiguous": bool(result and result.def_files > 1),
            # JSON must carry the same honesty as the text output — agents
            # binding to this shape must see the confidence level.
            "confidence": REFS_CONFIDENCE,
        }
        if len(parts) >= 2:
            payload["qualified"] = parts
        print(json.dumps(payload, ensure_ascii=False))
        store.close()
        return 0
    out = refsview.render_refs(store, args.name, limit=args.limit)
    store.close()
    if out == f"(`{args.name}` not found in index)":
        print(out)
        return 1
    print(out)
    return 0


def cmd_change(args) -> int:
    from lotsman import changeview
    root = _root(args)
    store = _open(root)
    if args.json:
        rep = changeview.collect_change(store, args.target)
        store.close()
        if rep is None:
            print(json.dumps({"target": args.target, "found": False}))
            return 1
        row = lambda r: {"path": r.path, "line": r.line, "signature": r.signature}  # noqa: E731
        print(json.dumps({
            "target": args.target, "owner": rep.owner, "member": rep.member,
            "definitions": [row(r) for r in rep.defs],
            "declared_by": [{"type": i.type_name, **row(i.row)} for i in rep.declared_by],
            "implementations": [{"type": i.type_name, **row(i.row)} for i in rep.impls],
            "subtypes": [{"type": t, "path": p, "line": ln} for t, p, ln in rep.subtypes],
            "callers": [{"path": s.path, "lines": s.lines, "same_line": s.same_line,
                         "test": is_test_path(s.path)} for s in rep.callers],
            "data": [{"path": s.path, "lines": s.lines, "type_named": s.same_line}
                     for s in rep.data],
            "name_only_files": rep.name_only,
            "extension_method": rep.extension,
            "ambiguous_owners": rep.ambiguous_owners,
            "not_covered": changeview.NOT_COVERED,
            "confidence": REFS_CONFIDENCE,
        }, ensure_ascii=False))
        return 0
    out = changeview.render_change(store, args.target, limit=args.limit)
    store.close()
    print(out)
    return 1 if out.startswith("(") else 0


def cmd_unity(args) -> int:
    from lotsman import unity
    root = _root(args)
    store = _open(root)
    out = unity.render_unity(store, args.target, args.uses, root=root)
    store.close()
    print(out)
    return 1 if out.startswith("(") else 0


def cmd_impact(args) -> int:
    from lotsman import impact
    root = _root(args)
    store = _open(root)
    indexer.index_repo(root, store)  # impact must see the current disk state
    if args.files:
        changed, method = list(args.files), "explicit"
    else:
        changed, method = impact.detect_changed(root, store, args.since)
    out = impact.generate_impact(store, changed, budget=args.budget,
                                 tests_only=args.tests)
    store.close()
    print(out, end="")
    print(f"[lotsman] impact via {method}", file=sys.stderr)
    return 0


def cmd_init(args) -> int:
    from lotsman.init_cmd import run_init
    return run_init(_root(args), agents=args.agent or [],
                    no_index=args.no_index)


def cmd_update(args) -> int:
    from lotsman.init_cmd import run_update
    return run_update(_root(args), agents=args.agent,
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


def cmd_report(args) -> int:
    print(querylog.summarize(_root(args)))
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

    sp = sub.add_parser("update", help="refresh managed agent artifacts and "
                                       "the project index after lotsman or "
                                       "source changes")
    sp.add_argument("--agent", action="append",
                    choices=["claude", "codex", "cursor"],
                    help="refresh/add this agent-specific config (repeatable); "
                         "default: auto-detect existing artifacts")
    sp.add_argument("--no-index", action="store_true",
                    help="refresh generated artifacts only")
    sp.set_defaults(fn=cmd_update)

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
    sp.add_argument("--docs", type=int, default=search_mod.DOCS_LIMIT,
                    help="Markdown sections to add after code hits "
                         f"(default {search_mod.DOCS_LIMIT}; 0 = none)")
    sp.add_argument("--json", action="store_true")
    sp.set_defaults(fn=cmd_search)

    sp = sub.add_parser("outline", help="symbol skeleton of one file")
    sp.add_argument("file", help="path relative to repo root")
    sp.add_argument("--json", action="store_true")
    sp.set_defaults(fn=cmd_outline)

    sp = sub.add_parser("slice",
                        help="one symbol's full body plus a file skeleton")
    sp.add_argument("file", help="path relative to repo root")
    sp.add_argument("name")
    sp.set_defaults(fn=cmd_slice)

    sp = sub.add_parser("defs", help="where a symbol is defined")
    sp.add_argument("name")
    sp.add_argument("--json", action="store_true")
    sp.set_defaults(fn=cmd_defs)

    sp = sub.add_parser("refs", help="who references a symbol")
    sp.add_argument("name")
    sp.add_argument("-k", "--limit", type=int, default=20)
    sp.add_argument("--json", action="store_true")
    sp.set_defaults(fn=cmd_refs)

    sp = sub.add_parser("change", help="before changing a symbol: definition, "
                                       "overrides, callers file:line in its type family")
    sp.add_argument("target", help="Type.Member, Member or Type")
    sp.add_argument("-k", "--limit", type=int, default=25, help="caller files shown")
    sp.add_argument("--json", action="store_true")
    sp.set_defaults(fn=cmd_change)

    sp = sub.add_parser("unity", help="Unity project facts: the assembly (asmdef) "
                                      "compiling a file/type, its references, who "
                                      "references it, #if branches per target")
    sp.add_argument("target", help="file path, type/symbol name or assembly name")
    sp.add_argument("uses", nargs="?", help="type, file or assembly the target's "
                                            "code wants to use: is it visible?")
    sp.set_defaults(fn=cmd_unity)

    sp = sub.add_parser("stats", help="index statistics")
    sp.set_defaults(fn=cmd_stats)

    sp = sub.add_parser("report", help="summarize opt-in MCP query telemetry")
    sp.set_defaults(fn=cmd_report)

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
    sp.add_argument("--tests", action="store_true",
                    help="show only impacted test files")
    sp.set_defaults(fn=cmd_impact)

    sp = sub.add_parser("mcp", help="run MCP stdio server (tools: map, search, "
                                    "outline, slice, defs, refs, change, "
                                    "unity, impact)")
    sp.set_defaults(fn=cmd_mcp)

    args = p.parse_args(argv)
    if args.cmd in LOGGED_COMMANDS:
        root = Path(args.repo).resolve()
        if querylog.enabled(root):
            return _run_logged(args, root)
    return args.fn(args)


# Read commands whose use is worth measuring (`lotsman report`).
LOGGED_COMMANDS = frozenset({"map", "search", "outline", "slice", "defs",
                             "refs", "impact", "change", "unity"})


def _run_logged(args, root: Path) -> int:
    """Run a command while teeing stdout, then log what the caller saw."""
    real, captured = sys.stdout, io.StringIO()

    class _Tee:
        def write(self, text: str) -> int:
            captured.write(text)
            return real.write(text)

        def flush(self) -> None:
            real.flush()

        def __getattr__(self, name):  # encoding, isatty, ...
            return getattr(real, name)

    sys.stdout = _Tee()
    try:
        code = args.fn(args)
    finally:
        sys.stdout = real
    params = {k: v for k, v in vars(args).items()
              if k not in ("fn", "cmd", "repo") and v not in (None, False, [])}
    querylog.log(root, args.cmd, params, captured.getvalue(), via="cli")
    return code
