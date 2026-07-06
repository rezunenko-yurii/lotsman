#!/usr/bin/env python3
"""Reproducible benchmark: lotsman on the WordPress codebase.

Measures indexing (cold / no-op / single-file change), map (cold / warm),
search latency, and token cost for a realistic PHP navigation scenario versus
reading whole files.

Usage:
    python benchmarks/bench_wordpress.py [--wordpress-dir PATH] [--keep]

Without --wordpress-dir the script downloads the pinned WordPress release zip
from wordpress.org into a temp directory (network required) and removes it
afterwards unless --keep.
"""

from __future__ import annotations

import argparse
import shutil
import sys
import tempfile
import time
import urllib.request
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lotsman import embed, indexer, repomap, search  # noqa: E402
from lotsman.textutil import estimate_tokens  # noqa: E402

WORDPRESS_VERSION = "7.0"
WORDPRESS_ZIP_URL = f"https://wordpress.org/wordpress-{WORDPRESS_VERSION}.zip"

MAP_MUST_CONTAIN = [
    "wp-includes/functions.php",
    "wp-includes/plugin.php",
    "wp-includes/class-wpdb.php",
    "wp-includes/class-wp-query.php",
]
DEF_MUST_DEFINE = [
    ("sanitize_text_field", "wp-includes/formatting.php"),
]
NAV_QUESTIONS = [
    ("rest api dispatch request", "wp-includes/rest-api/class-wp-rest-server.php", 5),
    ("enqueue scripts styles", "wp-includes/script-loader.php", 5),
    ("parse blocks gutenberg", "wp-includes/class-wp-block-parser.php", 5),
]
WORDPRESS_IGNORE = """\
# WordPress release zips include large bundled third-party packages. The
# benchmark measures navigation in WordPress core, not vendor/library ranking.
wp-includes/js/
wp-includes/SimplePie/
wp-includes/Requests/
wp-includes/sodium_compat/
wp-includes/PHPMailer/
wp-includes/ID3/
wp-includes/php-ai-client/
wp-admin/js/
wp-content/
"""


def timed(fn, *args, **kwargs):
    t0 = time.monotonic()
    result = fn(*args, **kwargs)
    return result, time.monotonic() - t0


def get_wordpress(path: str | None) -> tuple[Path, bool]:
    if path:
        return Path(path).resolve(), False
    tmp = Path(tempfile.mkdtemp(prefix="lotsman_bench_wp_"))
    archive = tmp / f"wordpress-{WORDPRESS_VERSION}.zip"
    print(f"downloading wordpress {WORDPRESS_VERSION} into {archive} ...")
    urllib.request.urlretrieve(WORDPRESS_ZIP_URL, archive)
    print(f"extracting {archive.name} ...")
    with zipfile.ZipFile(archive) as zf:
        zf.extractall(tmp)
    return tmp / "wordpress", True


def prepare_fixture(root: Path) -> None:
    """Keep the benchmark focused on WordPress core, not bundled dependencies."""
    (root / ".lotsmanignore").write_text(WORDPRESS_IGNORE)


def scenario_tokens(root: Path, store) -> tuple[int, int]:
    """Agent scenario: locate text-field sanitization in WordPress.

    lotsman path: search -> defs -> outline -> read the function slice.
    naive path:   read the likely whole files an agent would inspect.
    """
    tool_output = ""
    hits = search.search(store, "sanitize_text_field", limit=5)
    tool_output += "\n".join(
        f"{h.symbol.path}:{h.symbol.line} {h.symbol.signature}" for h in hits)
    defs = store.symbols_named("sanitize_text_field")
    tool_output += "\n".join(f"{d.path}:{d.line} {d.signature}" for d in defs)
    outline = store.symbols_in_file("wp-includes/formatting.php")
    tool_output += "\n".join(
        f"{r.line}-{r.end_line} {r.signature}" for r in outline)

    formatting = (root / "wp-includes/formatting.php").read_text(
        errors="replace")
    target = next(d for d in defs if d.path == "wp-includes/formatting.php")
    slice_text = "\n".join(
        formatting.splitlines()[max(target.line - 8, 0):target.line + 80])
    smart = estimate_tokens(tool_output) + estimate_tokens(slice_text)

    naive = sum(estimate_tokens((root / p).read_text(errors="replace")) for p in (
        "wp-includes/formatting.php",
        "wp-includes/option.php",
        "wp-admin/includes/schema.php"))
    return smart, naive


def quality_checks(store) -> list[tuple[str, bool]]:
    checks: list[tuple[str, bool]] = []
    map_text = repomap.generate_map(store, budget=2048)
    for sym in MAP_MUST_CONTAIN:
        checks.append((f"map contains {sym}", sym in map_text))
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
    ap.add_argument("--wordpress-dir",
                    help="existing WordPress checkout/extract (skips download)")
    ap.add_argument("--keep", action="store_true", help="keep the temp extract")
    args = ap.parse_args()

    root, is_temp = get_wordpress(args.wordpress_dir)
    rows: list[tuple[str, str]] = []
    try:
        prepare_fixture(root)
        shutil.rmtree(root / ".lotsman", ignore_errors=True)
        store = indexer.open_store(root)

        res, t = timed(indexer.index_repo, root, store)
        rows.append(("cold index", f"{t:.2f}s ({res.scanned} files)"))
        n, t = timed(embed.embed_missing, store)
        rows.append(("embedding pass",
                     f"{t:.2f}s ({n} symbols)" if n else "skipped — no model2vec"))
        res, t = timed(indexer.index_repo, root, store)
        rows.append(("no-op reindex", f"{t:.2f}s"))

        target = root / "wp-includes/formatting.php"
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

        _, t = timed(search.search, store, "sanitize_text_field", 10)
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
    print(f"\nlotsman benchmark — wordpress {WORDPRESS_VERSION}")
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
