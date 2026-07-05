"""Environment and index health check: surfaces every silent degradation
(missing grammars, unavailable embeddings, stale index, ignore rules)."""

from __future__ import annotations

import sys
from pathlib import Path

from lotsman import embed, indexer, scanner
from lotsman.extract import DEF_QUERIES, REF_QUERIES, _compile

OK, WARN, FAIL = "ok", "warn", "FAIL"


def _check_languages() -> tuple[str, list[str]]:
    lines = []
    broken = 0
    for lang in sorted(DEF_QUERIES):
        defs = "tree-sitter" if _compile(lang, "def") else "regex-fallback"
        if lang in REF_QUERIES:
            refs = "tree-sitter" if _compile(lang, "ref") else "lexical-fallback"
        else:
            refs = "lexical"
        if defs != "tree-sitter":
            broken += 1
        lines.append(f"    {lang:<12} defs: {defs:<16} refs: {refs}")
    status = FAIL if broken == len(DEF_QUERIES) else (WARN if broken else OK)
    return status, lines


def _check_embeddings() -> tuple[str, list[str]]:
    if not embed.available():
        return WARN, ["    model2vec unavailable — search runs BM25-only",
                      "    fix: pip install \"lotsman[embeddings]\""]
    vec = embed.encode(["probe"])
    return OK, [f"    model loaded, {vec.shape[1]} dimensions"]


def _check_index(root: Path) -> tuple[str, list[str]]:
    db = root / indexer.DB_RELPATH
    if not db.exists():
        return WARN, ["    no index yet — any read command will build it"]
    store = indexer.open_store(root)
    try:
        stats = store.stats()
        version = store.get_meta("index_version")
        lines = [f"    {stats['files']} files, {stats['symbols']} symbols, "
                 f"{store.vector_count()} vectors, "
                 f"{stats['db_bytes'] // 1024} KiB"]
        status = OK
        if version != indexer.INDEX_VERSION:
            lines.append(f"    index version {version} != {indexer.INDEX_VERSION} "
                         "— next index run does a full rebuild")
            status = WARN
        # Freshness: how many files the incremental pass would touch now.
        known = store.known_files()
        records = scanner.scan(root)
        changed = sum(1 for r in records
                      if (p := known.get(r.path)) is None
                      or p[1] != r.mtime or p[2] != r.size)
        gone = len(known) - sum(1 for r in records if r.path in known)
        if changed or gone:
            lines.append(f"    stale: {changed} changed/new, {gone} removed "
                         "since last index — run `lotsman index`")
            status = WARN
        else:
            lines.append("    index is fresh (matches the working tree)")
        stamp_ok = store.get_meta("rank_cache_stamp") == store.state_stamp()
        lines.append(f"    rank cache: {'warm' if stamp_ok else 'cold (first map will compute)'}")
    finally:
        store.close()
    return status, lines


def _check_ignore(root: Path) -> tuple[str, list[str]]:
    patterns = scanner.load_ignore_patterns(root)
    if not patterns:
        return OK, [f"    no {scanner.IGNORE_FILE} — everything scannable is indexed"]
    return OK, [f"    {len(patterns)} pattern(s) in {scanner.IGNORE_FILE}"]


def _check_change_detection(root: Path) -> tuple[str, list[str]]:
    import subprocess
    try:
        r = subprocess.run(["git", "-C", str(root), "rev-parse"],
                           capture_output=True, timeout=10)
        if r.returncode == 0:
            return OK, ["    impact uses `git status`"]
    except (OSError, subprocess.TimeoutExpired):
        pass
    return OK, ["    not a git repo — impact falls back to mtime window (--since)"]


def run_doctor(root: Path) -> int:
    checks = [
        ("python", OK, [f"    {sys.version.split()[0]}"]),
        ("languages", *_check_languages()),
        ("embeddings", *_check_embeddings()),
        ("index", *_check_index(root)),
        ("ignore rules", *_check_ignore(root)),
        ("change detection", *_check_change_detection(root)),
    ]
    worst = OK
    for name, status, lines in checks:
        mark = {OK: "+", WARN: "!", FAIL: "x"}[status]
        print(f"[{mark}] {name} ({status})")
        for line in lines:
            print(line)
        if status == FAIL or (status == WARN and worst != FAIL):
            worst = status
    return 1 if worst == FAIL else 0
