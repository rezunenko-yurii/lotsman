#!/usr/bin/env python3
"""Reference-recall benchmark on a C#/Unity codebase: `refs` vs `grep -rlw`.

For every C# declaration with a corpus-unique name, compares the files lotsman
reports as referencing it against two baselines:
  grep  — files where the name occurs as a whole word (`grep -rlw` semantics);
  code  — the same, after blanking comments, string literals, preprocessor
          directives and namespace names with tree-sitter: the fair target
          for an AST-based index.
Both exclude the defining file, like `refs` itself does, and both are limited
to the files lotsman indexes (`.lotsmanignore` applies). "code" also blanks
namespace names (`namespace A.Editor`, `using A.Editor;`), which name no type.

A map section checks the default map for names flagged as noise (repo members
colliding with BCL/Unity API) and `~` folders, and how many files of a
`--mention` map are direct neighbors of the mentioned type.

Results are grouped by declaration kind (static class, enum, interface, ...),
so a construct lotsman cannot see shows up as a low-recall row, not as noise
averaged away. Curated symbols get a per-symbol row and, with --explain, the
code lines lotsman missed — for manual classification of the misses.

The corpus is only read: the index lives in a scratch directory (--db), never
in the corpus's own `.lotsman/`.

Usage:
    python benchmarks/bench_unity.py --root PATH/Assets [--db DIR]
        [--symbol NAME ...] [--explain NAME ...] [--json OUT]
"""

from __future__ import annotations

import argparse
import json
import re
import statistics
import sys
import tempfile
import time
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lotsman import indexer, refsview, scanner  # noqa: E402
from lotsman.store import Store  # noqa: E402
from lotsman.textutil import STOPWORDS  # noqa: E402

WORD_RE = re.compile(rb"[A-Za-z_][A-Za-z0-9_]*")
PREPROC_LINE_RE = re.compile(rb"^[ \t]*#.*$", re.M)
# Nodes whose text is not code for reference purposes.
NON_CODE_NODES = frozenset({
    "comment", "string_literal", "verbatim_string_literal",
    "raw_string_literal", "character_literal", "string_content",
    "preproc_arg",
})
# Default curated symbols: the ones the independent review flagged, plus one
# of each remaining kind. Missing names are skipped with a note.
DEFAULT_SYMBOLS = ["ExLog", "UiClasses", "WindowDisplayMode", "WindowsManager"]


def _parser():
    import tree_sitter
    from tree_sitter_language_pack import get_language
    return tree_sitter.Parser(get_language("csharp"))


def _blank_non_code(parser, data: bytes) -> tuple[bytes, object]:
    """Blank what cannot reference a symbol: comments, strings, #directives,
    and namespace names (`namespace A.Editor`, `using A.Editor;` — a class
    `Editor` is not used there; `using static` and aliases are kept)."""
    tree = parser.parse(data)
    buf = bytearray(data)

    def blank(node):
        for i in range(node.start_byte, node.end_byte):
            if buf[i] not in (10, 13):  # keep line structure
                buf[i] = 32

    stack = [tree.root_node]
    while stack:
        node = stack.pop()
        if node.type in NON_CODE_NODES:
            blank(node)
            continue
        if node.type == "using_directive":
            text = data[node.start_byte:node.end_byte]
            if b"static" not in text and b"=" not in text:
                blank(node)
            continue
        if node.type in ("namespace_declaration",
                         "file_scoped_namespace_declaration"):
            name = node.child_by_field_name("name")
            if name is not None:
                blank(name)
        stack.extend(node.children)
    code = PREPROC_LINE_RE.sub(lambda m: b" " * len(m.group(0)), bytes(buf))
    return code, tree


def _modifiers(node, data: bytes) -> set[str]:
    return {data[c.start_byte:c.end_byte].decode() for c in node.children
            if c.type == "modifier"}


def _classify(node, data: bytes) -> str | None:
    t = node.type
    if t == "class_declaration":
        mods = _modifiers(node, data)
        return "static class" if "static" in mods else "class"
    return {
        "enum_declaration": "enum",
        "interface_declaration": "interface",
        "struct_declaration": "struct",
        "method_declaration": "method",
        "property_declaration": "property",
        "delegate_declaration": "delegate",
    }.get(t)


def build_truth(root: Path, paths: list[str]):
    """Per-file word sets (raw and code-only) plus declarations by kind."""
    parser = _parser()
    raw_files: dict[str, set[str]] = defaultdict(set)
    code_files: dict[str, set[str]] = defaultdict(set)
    decls: dict[str, list[tuple[str, str, int]]] = defaultdict(list)  # name -> [(kind, path, line)]
    code_text: dict[str, bytes] = {}
    for path in paths:
        data = (root / path).read_bytes()
        for m in set(WORD_RE.findall(data)):
            raw_files[m.decode()].add(path)
        code, tree = _blank_non_code(parser, data)
        code_text[path] = code
        for m in set(WORD_RE.findall(code)):
            code_files[m.decode()].add(path)
        stack = [tree.root_node]
        while stack:
            node = stack.pop()
            kind = _classify(node, data)
            if kind:
                name_node = node.child_by_field_name("name")
                if name_node is not None:
                    name = data[name_node.start_byte:name_node.end_byte].decode()
                    decls[name].append((kind, path, name_node.start_point[0] + 1))
            stack.extend(node.children)
    return raw_files, code_files, decls, code_text


def lotsman_files(store: Store, name: str) -> set[str]:
    return {path for path, _ in store.files_referencing(name)}


def evaluate(store, raw_files, code_files, decls):
    """Per-kind recall over corpus-unique names; returns rows and per-name data."""
    per_kind: dict[str, dict] = defaultdict(lambda: {
        "symbols": 0, "grep": 0, "code": 0, "found": 0, "found_in_code": 0,
        "recalls": [], "zero": 0})
    per_name = {}
    for name, entries in decls.items():
        if len(entries) != 1 or len(name) < 3 or name in STOPWORDS:
            continue
        kind, def_path, _ = entries[0]
        grep = raw_files.get(name, set()) - {def_path}
        code = code_files.get(name, set()) - {def_path}
        if not code:
            continue  # unreferenced (or only in comments): nothing to recall
        found = lotsman_files(store, name) - {def_path}
        hit = found & code
        row = per_kind[kind]
        row["symbols"] += 1
        row["grep"] += len(grep)
        row["code"] += len(code)
        row["found"] += len(found)
        row["found_in_code"] += len(hit)
        row["recalls"].append(len(hit) / len(code))
        if not found:
            row["zero"] += 1
        per_name[name] = (kind, def_path, grep, code, found)
    return per_kind, per_name


KIND_ORDER = ["static class", "enum", "class", "interface", "struct",
              "delegate", "property", "method"]
# Quality gates (exit 1 on failure): type-like kinds must be found close to
# the comment/string-stripped grep baseline. Measured 2026-09-10 on a private
# Unity corpus: 93.6-100% (enum residue = same-named enum members elsewhere).
# Properties and methods are not gated: camelCase members are skipped by
# design (C# locals look the same) and PascalCase ones collide by name.
GATED_KINDS = ("static class", "enum", "class", "interface", "struct")
MIN_RECALL = 0.90


def render_kinds(per_kind) -> str:
    lines = [f"{'kind':<13} {'syms':>5} {'grep':>6} {'code':>6} {'lots':>6} "
             f"{'recall':>7} {'macro':>6} {'prec':>5} {'zero':>5}"]
    for kind in KIND_ORDER:
        r = per_kind.get(kind)
        if not r or not r["symbols"]:
            continue
        recall = r["found_in_code"] / r["code"]
        prec = r["found_in_code"] / r["found"] if r["found"] else 1.0
        lines.append(
            f"{kind:<13} {r['symbols']:>5} {r['grep']:>6} {r['code']:>6} "
            f"{r['found']:>6} {recall:>7.1%} {statistics.mean(r['recalls']):>6.1%} "
            f"{prec:>5.0%} {r['zero']:>5}")
    return "\n".join(lines)


def curated_rows(names, per_name, all_raw: dict[str, int]) -> str:
    lines = [f"{'symbol':<28} {'kind':<12} {'grep*':>6} {'grep':>5} {'code':>5} "
             f"{'lots':>5} {'recall':>7}"]
    for name in names:
        if name not in per_name:
            lines.append(f"{name:<28} (not a unique referenced declaration)")
            continue
        kind, _, grep, code, found = per_name[name]
        recall = len(found & code) / len(code)
        lines.append(f"{name:<28} {kind:<12} {all_raw.get(name, 0):>6} "
                     f"{len(grep):>5} {len(code):>5} {len(found):>5} {recall:>7.1%}")
    lines.append("grep* = grep -rlw over every *.cs under root (vendor dirs "
                 "included); other columns exclude the defining file")
    return "\n".join(lines)


def explain(name, per_name, code_text, limit=12) -> str:
    if name not in per_name:
        return f"{name}: not evaluated"
    kind, _, grep, code, found = per_name[name]
    missed = sorted(code - found)
    out = [f"--- {name} [{kind}]: {len(missed)} code files missed, "
           f"{len(grep - code)} grep-only files (comments/strings)"]
    pat = re.compile(rb"\b" + re.escape(name.encode()) + rb"\b")
    shown = 0
    for path in missed:
        for lineno, line in enumerate(code_text[path].split(b"\n"), 1):
            if pat.search(line):
                out.append(f"  {path}:{lineno}: {line.decode(errors='replace').strip()[:110]}")
                shown += 1
                break
        if shown >= limit:
            break
    return "\n".join(out)


def count_all_cs(root: Path, names: list[str]) -> dict[str, int]:
    """grep -rlw over every *.cs under root, ignoring .lotsmanignore."""
    wanted = {n.encode() for n in names}
    counts = dict.fromkeys(names, 0)
    for path in root.rglob("*.cs"):
        try:
            words = set(WORD_RE.findall(path.read_bytes()))
        except OSError:
            continue
        for w in wanted & words:
            counts[w.decode()] += 1
    return counts


# Names the review found topping the default map: repo members that collide
# with BCL/Unity API (`dict.TryGetValue`, `Encoding.GetBytes`, `System.Action`).
MAP_NOISE_NAMES = ["TryGetValue", "Action", "Empty", "GetBytes",
                   "IsNullOrEmpty", "ToArray", "Exception", "Random"]
MAP_NOISE_RE = re.compile(r"\b(" + "|".join(MAP_NOISE_NAMES) + r")\s*[(<{;=]")


def map_quality(store: Store, code_files, decls, code_text, mention: str) -> str:
    """Default map: flagged generic names and Unity-hidden (`~`) paths among
    its entries. Mention map: share of files that are direct neighbors of the
    mentioned type — its file, files using it, files defining types it uses
    (from the comment/string-stripped code, independent of lotsman)."""
    from lotsman import repomap
    t0 = time.monotonic()
    default = repomap.generate_map(store, budget=1500)
    default_s = time.monotonic() - t0
    rows = [line for line in default.splitlines() if line.startswith(" ")]
    files = [line[:-1] for line in default.splitlines() if line.endswith(":")]
    noisy = sorted({m.group(1) for line in rows for m in [MAP_NOISE_RE.search(line)] if m})
    hidden = [f for f in files if any(p.endswith("~") for p in f.split("/"))]
    out = [f"default map: {len(files)} files, {len(rows)} entries, {len(default)} chars, "
           f"{default_s:.2f}s; flagged generic names: {', '.join(noisy) or 'none'}; "
           f"files under ~ dirs: {len(hidden)}"]
    if mention in decls:
        def_paths = {p for _, p, _ in decls[mention]}
        used = set()
        for path in def_paths:
            words = set(WORD_RE.findall(code_text.get(path, b"")))
            used |= {p for w in words for _, p, _ in decls.get(w.decode(), [])}
        neighbors = def_paths | code_files.get(mention, set()) | used
        t0 = time.monotonic()
        mapped = repomap.generate_map(store, budget=1500, mentions={mention})
        mention_s = time.monotonic() - t0
        mfiles = [line[:-1] for line in mapped.splitlines() if line.endswith(":")]
        near = sum(1 for f in mfiles if f in neighbors)
        out.append(f"--mention {mention}: {near}/{len(mfiles)} files are direct "
                   f"neighbors ({len(mapped)} chars, {mention_s:.2f}s); "
                   f"first 5: {', '.join(f.rsplit('/', 1)[-1] for f in mfiles[:5])}")
    return "\n".join(out)


def time_refs(store: Store, names: list[str]) -> tuple[float, dict[str, int]]:
    sizes = {}
    t0 = time.monotonic()
    for name in names:
        sizes[name] = len(refsview.render_refs(store, name))
    return (time.monotonic() - t0) / max(len(names), 1), sizes


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--root", required=True, help="corpus root (Unity Assets/)")
    ap.add_argument("--db", help="scratch dir for the index (default: temp)")
    ap.add_argument("--symbol", action="append", help="curated symbol (repeatable)")
    ap.add_argument("--explain", action="append", default=[],
                    help="show code lines lotsman missed for this symbol")
    ap.add_argument("--mention", default="WindowsManager",
                    help="type for the personalized-map check")
    ap.add_argument("--json", help="write machine-readable results here")
    args = ap.parse_args()

    root = Path(args.root).resolve()
    db_dir = Path(args.db) if args.db else Path(tempfile.mkdtemp(prefix="lotsman_unity_"))
    store = Store(db_dir / "index.db")
    t0 = time.monotonic()
    res = indexer.index_repo(root, store)
    index_s = time.monotonic() - t0
    paths = sorted(r.path for r in scanner.scan(root) if r.lang == "csharp")
    print(f"index: {len(paths)} C# files, +{res.added} ~{res.updated} in {index_s:.1f}s "
          f"(db {db_dir})")

    t0 = time.monotonic()
    raw_files, code_files, decls, code_text = build_truth(root, paths)
    print(f"truth built in {time.monotonic() - t0:.1f}s")

    per_kind, per_name = evaluate(store, raw_files, code_files, decls)
    print("\nrefs recall by declaration kind (corpus-unique names, defining file excluded)")
    print("grep = whole-word files; code = grep minus comments, strings, #directives")
    print("and namespace names (namespace/using lines);")
    print("lots = files lotsman reports; recall = lots∩code / code (micro), macro = mean")
    print("per symbol; prec = lots∩code / lots; zero = symbols with 0 files reported\n")
    print(render_kinds(per_kind))

    names = args.symbol or DEFAULT_SYMBOLS
    all_raw = count_all_cs(root, names)
    print("\n" + curated_rows(names, per_name, all_raw))

    avg_s, sizes = time_refs(store, names)
    print(f"\nrefs render: {avg_s * 1000:.1f} ms/symbol (store query + render); "
          "output chars: " + ", ".join(f"{n}={c}" for n, c in sizes.items()))

    print("\n" + map_quality(store, code_files, decls, code_text, args.mention))

    for name in args.explain:
        print("\n" + explain(name, per_name, code_text))

    failures = [f"{kind}: recall {r['found_in_code'] / r['code']:.1%} < {MIN_RECALL:.0%}"
                for kind, r in per_kind.items()
                if kind in GATED_KINDS and r["code"]
                and r["found_in_code"] / r["code"] < MIN_RECALL]
    print("\nquality gates: " + ("FAIL — " + "; ".join(failures) if failures else "pass"))

    if args.json:
        payload = {
            "files": len(paths),
            "index_seconds": round(index_s, 2),
            "kinds": {k: {key: v for key, v in r.items() if key != "recalls"}
                      | {"macro": round(statistics.mean(r["recalls"]), 4)}
                      for k, r in per_kind.items()},
            "curated": {n: {"kind": per_name[n][0], "grep_all": all_raw.get(n, 0),
                            "grep": len(per_name[n][2]), "code": len(per_name[n][3]),
                            "found": len(per_name[n][4])}
                        for n in names if n in per_name},
            "refs_chars": sizes,
        }
        Path(args.json).write_text(json.dumps(payload, indent=2, ensure_ascii=False))
    store.close()
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
