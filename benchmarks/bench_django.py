#!/usr/bin/env python3
"""Reproducible benchmark: lotsman on the Django codebase.

Measures indexing (cold / no-op / single-file change), map (cold / warm),
search latency, and the token cost of a realistic agent navigation scenario
("how does Django validate model field uniqueness") versus reading whole files.

Usage:
    python benchmarks/bench_django.py [--django-dir PATH] [--keep]

Without --django-dir the script shallow-clones Django at a pinned tag into a
temp directory (network required) and removes it afterwards unless --keep.
"""

from __future__ import annotations

import argparse
import contextlib
import io
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lotsman import embed, indexer, repomap, search  # noqa: E402
from lotsman.textutil import estimate_tokens  # noqa: E402

DJANGO_REPO = "https://github.com/django/django.git"
DJANGO_TAG = "5.2"  # pinned for reproducibility

# --- quality baseline (regression guard, see docs/DESIGN.md) ----------------
# The default map must surface genuinely central Django internals...
MAP_MUST_CONTAIN = ["cached_property", "ValidationError", "ForeignKey"]
# ...and must not be topped by generic method names — that pattern indicated
# ranking regressions during development.
MAP_MUST_NOT_MATCH = r"def (value|list|request|data)\("
# Navigation questions: the expected file must appear in the top-k hits.
NAV_QUESTIONS = [
    ("validate unique fields model", "django/db/models/base.py", 5),
    ("http response redirect", "django/http/response.py", 5),
    ("clean form field value", "django/forms/fields.py", 5),
    ("template context push", "django/template/context.py", 5),
]


def timed(fn, *args, **kwargs):
    t0 = time.monotonic()
    result = fn(*args, **kwargs)
    return result, time.monotonic() - t0


def get_django(path: str | None) -> tuple[Path, bool]:
    if path:
        return Path(path).resolve(), False
    tmp = Path(tempfile.mkdtemp(prefix="lotsman_bench_"))
    print(f"cloning django @{DJANGO_TAG} (shallow) into {tmp} ...")
    subprocess.run(
        ["git", "clone", "--depth", "1", "--branch", DJANGO_TAG, "--quiet",
         DJANGO_REPO, str(tmp / "django")],
        check=True)
    return tmp / "django", True


def scenario_tokens(root: Path, store) -> tuple[int, int]:
    """Agent scenario: locate Django's model-uniqueness validation.

    lotsman path: search -> refs -> outline -> read only the relevant slice.
    naive path:   read the three relevant files whole.
    """
    tool_output = ""
    hits = search.search(store, "validate unique fields model", limit=5)
    tool_output += "\n".join(
        f"{h.symbol.path}:{h.symbol.line} {h.symbol.signature}" for h in hits)
    defs = store.symbols_named("validate_unique")
    tool_output += "\n".join(f"{d.path}:{d.line} {d.signature}" for d in defs)
    outline = store.symbols_in_file("django/db/models/base.py")
    tool_output += "\n".join(
        f"{r.line}-{r.end_line} {r.signature}" for r in outline)

    base = (root / "django/db/models/base.py").read_text()
    target = next(d for d in defs if d.path == "django/db/models/base.py")
    # the agent reads validate_unique plus the two helpers below it (~140 lines)
    slice_text = "\n".join(base.splitlines()[target.line - 1:target.line + 140])
    smart = estimate_tokens(tool_output) + estimate_tokens(slice_text)

    naive = sum(estimate_tokens((root / p).read_text()) for p in (
        "django/db/models/base.py",
        "django/forms/models.py",
        "django/db/models/query.py"))
    return smart, naive


def quality_checks(store) -> list[tuple[str, bool]]:
    """Deterministic quality gates; a failure means a ranking/search regression."""
    import re
    checks: list[tuple[str, bool]] = []
    map_text = repomap.generate_map(store, budget=2048)
    for sym in MAP_MUST_CONTAIN:
        checks.append((f"map contains {sym}", sym in map_text))
    checks.append((f"map has no generic tops ({MAP_MUST_NOT_MATCH})",
                   re.search(MAP_MUST_NOT_MATCH, map_text) is None))
    for query, expected, k in NAV_QUESTIONS:
        hits = search.search(store, query, limit=k)
        found = any(expected == h.symbol.path for h in hits)
        checks.append((f"search '{query}' -> {expected} in top-{k}", found))
    return checks


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--django-dir", help="existing Django checkout (skips clone)")
    ap.add_argument("--keep", action="store_true", help="keep the temp clone")
    args = ap.parse_args()

    root, is_temp = get_django(args.django_dir)
    rows: list[tuple[str, str]] = []
    try:
        shutil.rmtree(root / ".lotsman", ignore_errors=True)
        store = indexer.open_store(root)

        res, t = timed(indexer.index_repo, root, store)
        rows.append(("cold index", f"{t:.2f}s ({res.scanned} files)"))
        # Embed like the default CLI `index` does — quality gates are
        # calibrated for hybrid search.
        n, t = timed(embed.embed_missing, store)
        rows.append(("embedding pass",
                     f"{t:.2f}s ({n} symbols)" if n else "skipped — no model2vec"))
        res, t = timed(indexer.index_repo, root, store)
        rows.append(("no-op reindex", f"{t:.2f}s"))

        target = root / "django/db/models/query.py"
        target.write_text(target.read_text() + "\n# bench touch\n")
        res, t = timed(indexer.index_repo, root, store)
        rows.append(("reindex after 1-file edit", f"{t:.2f}s"))

        _, t = timed(repomap.generate_map, store, 2048)
        rows.append(("map (cold, computes ranks)", f"{t:.2f}s"))
        _, t = timed(repomap.generate_map, store, 2048)
        rows.append(("map (warm, rank cache)", f"{t:.2f}s"))

        _, t = timed(search.search, store, "validate email address", 10)
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
    print(f"\nlotsman benchmark — django @{DJANGO_TAG}")
    print("-" * (width + 20))
    for name, value in rows:
        print(f"{name:<{width}}  {value}")

    print("\nquality gates")
    if not embed.available():
        print("warning: model2vec unavailable — gates are calibrated for "
              "hybrid search and may misreport in BM25-only mode")
    print("-" * (width + 20))
    failed = 0
    for name, ok in quality:
        print(f"{'PASS' if ok else 'FAIL'}  {name}")
        failed += 0 if ok else 1
    if failed:
        print(f"\n{failed} quality gate(s) FAILED — ranking/search regression")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
