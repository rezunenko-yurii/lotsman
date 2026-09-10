"""Incremental indexing: scan -> diff against store -> extract changed files."""

from __future__ import annotations

import json
import os
import time
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from lotsman import csdefines, docs, extract, jsonrefs, scanner, unity, wiring
from lotsman.store import Store

DB_RELPATH = ".lotsman/index.db"
# Env override for the index file: benchmarks and CI caches keep an index
# outside a read-only or shared project (different lotsman versions on one
# index would rebuild it back and forth).
INDEX_ENV = "LOTSMAN_INDEX"

# Bump when extraction/schema semantics change: forces a clean full reindex.
INDEX_VERSION = "9"


@dataclass
class IndexResult:
    scanned: int = 0
    added: int = 0
    updated: int = 0
    removed: int = 0
    unchanged: int = 0
    errors: list[str] = field(default_factory=list)
    seconds: float = 0.0
    # lang -> reason, for indexed languages whose grammar failed to load.
    degraded: dict[str, str] = field(default_factory=dict)


MAX_LINE_LEN = 1000  # no human writes lines this long


def _looks_generated(data: bytes) -> bool:
    """Minified/bundled/generated code: symbols from it are search noise."""
    head = data[:65536]
    return any(len(line) > MAX_LINE_LEN for line in head.split(b"\n"))


def _binding_docs(root: Path, store: Store) -> set[str]:
    """Docs that some AGENTS.md declares binding (read in full)."""
    known = store.doc_paths()
    found: set[str] = set()
    for path in known:
        if path.rsplit("/", 1)[-1] in ("AGENTS.md", "CLAUDE.md"):
            try:
                text = (root / path).read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            found |= docs.binding_docs(path, text, known)
    return found


def db_path(root: Path) -> Path:
    env = os.environ.get(INDEX_ENV)
    return Path(env).expanduser() if env else root / DB_RELPATH


def open_store(root: Path) -> Store:
    return Store(db_path(root))


def index_repo(root: Path, store: Store, verify: bool = False) -> IndexResult:
    """Incremental reindex. With verify=True the mtime+size fast path is
    disabled and every file is re-hashed — catches the (rare) case of content
    changing without touching size or timestamp. Wiring config and index
    version changes trigger a full wipe before scanning, which also rebuilds
    derived rows like embeddings and rank cache on the next passes."""
    t0 = time.monotonic()
    result = IndexResult()
    patterns, _ = wiring.load(root)
    wiring_sha = wiring.config_sha(root)
    stored_wiring_sha = store.get_meta("wiring_sha") or ""
    version_changed = store.get_meta("index_version") != INDEX_VERSION
    wiring_changed = (stored_wiring_sha != wiring_sha
                      and (stored_wiring_sha or wiring_sha))
    records = scanner.scan(root)
    result.scanned = len(records)
    # A grammar that breaks (or recovers) changes what extraction produces:
    # rebuild instead of mixing tree-sitter and fallback rows. The check
    # compiles every query, so its result is reused while the environment
    # fingerprint stays the same.
    langs = {r.lang for r in records}
    fingerprint = extract.grammar_fingerprint(langs)
    stored_degraded = json.loads(store.get_meta("degraded") or "{}")
    if store.get_meta("grammar_fingerprint") == fingerprint:
        result.degraded = stored_degraded
    else:
        result.degraded = extract.grammar_failures(langs)
    degraded_changed = set(result.degraded) != set(stored_degraded)
    if version_changed or wiring_changed or degraded_changed:
        store.wipe()
    store.set_meta("index_version", INDEX_VERSION)
    store.set_meta("wiring_sha", wiring_sha)
    store.set_meta("grammar_fingerprint", fingerprint)
    store.set_meta("degraded", json.dumps(result.degraded, ensure_ascii=False))
    if scanner.is_unity_project(root):
        store.set_meta(unity.META_UNITY_ASSETS, "" if root.name == "Assets" else "Assets/")
    known = store.known_files()
    seen_paths = set()
    docs_changed = False

    for rec in records:
        seen_paths.add(rec.path)
        prev = known.get(rec.path)
        # Fast path: mtime+size unchanged -> skip without hashing.
        if (not verify and prev is not None
                and prev[1] == rec.mtime and prev[2] == rec.size):
            result.unchanged += 1
            continue
        try:
            data = rec.read_bytes(root)
        except OSError as e:
            result.errors.append(f"{rec.path}: {e}")
            continue
        sha = scanner.file_sha(data)
        if prev is not None and prev[0] == sha:
            # Content identical (touched file): refresh mtime only.
            store.conn.execute(
                "UPDATE files SET mtime=?, size=? WHERE path=?",
                (rec.mtime, rec.size, rec.path))
            result.unchanged += 1
            continue
        doc_text = None
        bases: list = []
        assembly = None
        directives = None
        try:
            if rec.lang == unity.ASMDEF_LANG:
                # Assembly definitions: facts for `unity`/outline, no symbols.
                # The GUID other asmdefs reference it by lives in the `.meta`.
                symbols, idents, lines = [], Counter(), {}
                meta = root / (rec.path + ".meta")
                info = unity.parse_asmdef(data, meta.read_bytes() if meta.is_file() else None)
                if info is not None:
                    kind = "asmref" if rec.path.endswith(".asmref") else "asmdef"
                    name = str(info["json"].get("name" if kind == "asmdef" else "reference") or "")
                    assembly = (kind, name, json.dumps(info, ensure_ascii=False))
            elif rec.lang == "markdown":
                # Prose has long lines by nature: no generated-file check. Docs
                # add sections, never references (refs stay about code).
                sections = docs.parse_sections(data, rec.path)
                symbols = [extract.Symbol(s.name, docs.SECTION_KIND, s.line,
                                          s.end_line, s.signature) for s in sections]
                idents, lines = Counter(), {}
                doc_text = {(s.name, s.line): (s.heading_terms, s.body_terms)
                            for s in sections}
                docs_changed = True
            elif rec.lang == "json":
                # Configs reference code by name; dumps, lock files and
                # generated maps are too big or not JSON (`.rc` in C projects).
                if (len(data) > jsonrefs.MAX_JSON_BYTES or _looks_generated(data)
                        or not jsonrefs.looks_like_json(data)):
                    symbols, idents, lines = [], Counter(), {}
                else:
                    keys, idents, lines = jsonrefs.extract_json(data)
                    symbols = [extract.Symbol(k.name, jsonrefs.KEY_KIND, k.line,
                                              k.end_line, k.signature) for k in keys]
            elif _looks_generated(data):
                symbols, idents, lines = [], Counter(), {}  # record the file only
            else:
                symbols = extract.extract_symbols(rec.lang, data)
                idents, lines, bases = extract.extract_refs_full(rec.lang, data)
                if rec.lang == "csharp":
                    directives = csdefines.scan(data)
                wired, wired_lines = wiring.apply_with_lines(patterns, data)
                idents.update(wired)
                for name, extra in wired_lines.items():
                    lines[name] = sorted(set(lines.get(name, ())) | extra)
        except Exception as e:  # a single bad file must not kill the index run
            result.errors.append(f"{rec.path}: {e}")
            continue
        store.upsert_file(
            rec.path, sha, rec.mtime, rec.size, rec.lang,
            [(s.name, s.kind, s.line, s.end_line, s.signature) for s in symbols],
            idents, lines, doc_text, bases, assembly, directives)
        if prev is None:
            result.added += 1
        else:
            result.updated += 1

    gone = [p for p in known if p not in seen_paths]
    if gone:
        store.delete_files(gone)
        result.removed = len(gone)
        docs_changed = docs_changed or any(p.endswith(".md") for p in gone)
    if docs_changed or store.get_meta("binding_docs") is None:
        store.set_meta("binding_docs", json.dumps(
            sorted(_binding_docs(root, store)), ensure_ascii=False))

    store.commit()
    if result.removed > 200:  # reclaim space after mass removals (.lotsmanignore edits)
        store.conn.execute("VACUUM")
    result.seconds = time.monotonic() - t0
    return result
