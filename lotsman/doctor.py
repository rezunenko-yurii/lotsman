"""Environment and index health check: surfaces every silent degradation
(missing grammars, unavailable embeddings, stale index, ignore rules).

Human-readable by default; `--json` emits a machine-readable report and
`--fail-on-warn` turns warnings into a non-zero exit for CI/agent gates.
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from pathlib import Path

from lotsman import embed, indexer, scanner, wiring
from lotsman.extract import DEF_QUERIES, REF_QUERIES, _compile

OK, WARN, FAIL = "ok", "warn", "fail"
_RANK = {OK: 0, WARN: 1, FAIL: 2}


@dataclass
class Check:
    name: str
    status: str
    details: list[str] = field(default_factory=list)
    data: dict = field(default_factory=dict)


def _check_python() -> Check:
    version = sys.version.split()[0]
    return Check("python", OK, [version], {"version": version})


def _check_languages() -> Check:
    details, data = [], {}
    broken = 0
    for lang in sorted(DEF_QUERIES):
        defs = "tree-sitter" if _compile(lang, "def") else "regex-fallback"
        if lang in REF_QUERIES:
            refs = "tree-sitter" if _compile(lang, "ref") else "lexical-fallback"
        else:
            refs = "lexical"
        if defs != "tree-sitter":
            broken += 1
        data[lang] = {"defs": defs, "refs": refs}
        details.append(f"{lang:<12} defs: {defs:<16} refs: {refs}")
    status = FAIL if broken == len(DEF_QUERIES) else (WARN if broken else OK)
    return Check("languages", status, details, data)


def _check_embeddings() -> Check:
    if not embed.available():
        return Check("embeddings", WARN,
                     ["model2vec unavailable — search runs BM25-only",
                      'fix: pip install "lotsman[embeddings]"'],
                     {"available": False})
    vec = embed.encode(["probe"])
    dims = int(vec.shape[1])
    return Check("embeddings", OK, [f"model loaded, {dims} dimensions"],
                 {"available": True, "dimensions": dims})


def _check_index(root: Path) -> Check:
    db = root / indexer.DB_RELPATH
    if not db.exists():
        return Check("index", WARN,
                     ["no index yet — any read command will build it"],
                     {"exists": False})
    store = indexer.open_store(root)
    try:
        stats = store.stats()
        version = store.get_meta("index_version")
        known = store.known_files()
        records = scanner.scan(root)
        changed = sum(1 for r in records
                      if (p := known.get(r.path)) is None
                      or p[1] != r.mtime or p[2] != r.size)
        gone = len(known) - sum(1 for r in records if r.path in known)
        cache_warm = store.get_meta("rank_cache_stamp") == store.state_stamp()
    finally:
        store.close()

    status = OK
    details = [f"{stats['files']} files, {stats['symbols']} symbols, "
               f"{stats['db_bytes'] // 1024} KiB"]
    if version != indexer.INDEX_VERSION:
        details.append(f"index version {version} != {indexer.INDEX_VERSION} "
                       "— next index run does a full rebuild")
        status = WARN
    if changed or gone:
        details.append(f"stale: {changed} changed/new, {gone} removed "
                       "since last index — run `lotsman index`")
        status = WARN
    else:
        details.append("index is fresh (matches the working tree)")
    details.append(f"rank cache: {'warm' if cache_warm else 'cold (first map will compute)'}")
    return Check("index", status, details, {
        "exists": True, "files": stats["files"], "symbols": stats["symbols"],
        "db_bytes": stats["db_bytes"], "version_match": version == indexer.INDEX_VERSION,
        "stale_files": changed, "removed_files": gone, "rank_cache_warm": cache_warm,
    })


def _check_ignore(root: Path) -> Check:
    patterns = scanner.load_ignore_patterns(root)
    if not patterns:
        return Check("ignore_rules", OK,
                     [f"no {scanner.IGNORE_FILE} — everything scannable is indexed"],
                     {"patterns": 0})
    return Check("ignore_rules", OK,
                 [f"{len(patterns)} pattern(s) in {scanner.IGNORE_FILE}"],
                 {"patterns": len(patterns)})


def _check_change_detection(root: Path) -> Check:
    import subprocess
    method = "mtime-window"
    try:
        r = subprocess.run(["git", "-C", str(root), "rev-parse"],
                           capture_output=True, timeout=10)
        if r.returncode == 0:
            method = "git-status"
    except (OSError, subprocess.TimeoutExpired):
        pass
    note = ("impact uses `git status`" if method == "git-status"
            else "not a git repo — impact falls back to mtime window (--since)")
    return Check("change_detection", OK, [note], {"method": method})


def _check_wiring(root: Path) -> Check:
    patterns, errors = wiring.load(root)
    status = WARN if errors else OK
    details = [f"wiring: {len(patterns)} patterns"]
    details.extend(errors)
    return Check("wiring", status, details, {
        "patterns": len(patterns),
        "errors": errors,
    })


def collect_checks(root: Path) -> list[Check]:
    return [
        _check_python(),
        _check_languages(),
        _check_embeddings(),
        _check_index(root),
        _check_ignore(root),
        _check_wiring(root),
        _check_change_detection(root),
    ]


def run_doctor(root: Path, as_json: bool = False,
               fail_on_warn: bool = False) -> int:
    checks = collect_checks(root)
    worst = max((c.status for c in checks), key=lambda s: _RANK[s])
    if as_json:
        print(json.dumps({
            "status": worst,
            "checks": [{"name": c.name, "status": c.status,
                        "details": c.details, **c.data} for c in checks],
        }, ensure_ascii=False, indent=2))
    else:
        for c in checks:
            mark = {OK: "+", WARN: "!", FAIL: "x"}[c.status]
            print(f"[{mark}] {c.name} ({c.status})")
            for line in c.details:
                print(f"    {line}")
    if worst == FAIL:
        return 1
    if worst == WARN and fail_on_warn:
        return 1
    return 0
