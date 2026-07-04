"""Incremental indexing: scan -> diff against store -> extract changed files."""

from __future__ import annotations

import time
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from codemap import extract, scanner
from codemap.store import Store

DB_RELPATH = ".codemap/index.db"

# Bump when extraction/schema semantics change: forces a clean full reindex.
INDEX_VERSION = "3"


@dataclass
class IndexResult:
    scanned: int = 0
    added: int = 0
    updated: int = 0
    removed: int = 0
    unchanged: int = 0
    errors: list[str] = field(default_factory=list)
    seconds: float = 0.0


MAX_LINE_LEN = 1000  # no human writes lines this long


def _looks_generated(data: bytes) -> bool:
    """Minified/bundled/generated code: symbols from it are search noise."""
    head = data[:65536]
    return any(len(line) > MAX_LINE_LEN for line in head.split(b"\n"))


def open_store(root: Path) -> Store:
    return Store(root / DB_RELPATH)


def index_repo(root: Path, store: Store) -> IndexResult:
    t0 = time.monotonic()
    result = IndexResult()
    if store.get_meta("index_version") != INDEX_VERSION:
        store.wipe()
        store.set_meta("index_version", INDEX_VERSION)
    records = scanner.scan(root)
    result.scanned = len(records)
    known = store.known_files()
    seen_paths = set()

    for rec in records:
        seen_paths.add(rec.path)
        prev = known.get(rec.path)
        # Fast path: mtime+size unchanged -> skip without hashing.
        if prev is not None and prev[1] == rec.mtime and prev[2] == rec.size:
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
        try:
            if _looks_generated(data):
                symbols, idents = [], Counter()  # record the file, index nothing
            else:
                symbols = extract.extract_symbols(rec.lang, data)
                idents = extract.extract_refs(rec.lang, data)
        except Exception as e:  # a single bad file must not kill the index run
            result.errors.append(f"{rec.path}: {e}")
            continue
        store.upsert_file(
            rec.path, sha, rec.mtime, rec.size, rec.lang,
            [(s.name, s.kind, s.line, s.end_line, s.signature) for s in symbols],
            idents)
        if prev is None:
            result.added += 1
        else:
            result.updated += 1

    gone = [p for p in known if p not in seen_paths]
    if gone:
        store.delete_files(gone)
        result.removed = len(gone)

    store.commit()
    if result.removed > 200:  # reclaim space after mass removals (.codemapignore edits)
        store.conn.execute("VACUUM")
    result.seconds = time.monotonic() - t0
    return result
