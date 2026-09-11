#!/usr/bin/env python3
"""How often `refs`, `change` and `impact` cut their output at the default
limit, and what reading the rest costs today (X4 evidence).

Samples C# types, methods (`Type.Member` for `change`) and files of an
existing index. For outputs cut at the default limit it reports the first
page and the full listing: today the rest costs a re-call with a bigger
limit, i.e. the first page read twice; a continuation cursor would cost only
the rest. Report only, no gate.

Usage:
    python benchmarks/bench_truncation.py --db PATH/index.db [--n 300] [--seed 7]
"""

from __future__ import annotations

import argparse
import random
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lotsman import changeview, impact, refsview  # noqa: E402
from lotsman.store import TYPE_KINDS, Store  # noqa: E402

UNLIMITED = 100_000


def tokens(text: str) -> int:
    return len(text) // 4


def report(label: str, outs: list[tuple[bool, int, int]]) -> None:
    if not outs:
        return
    cut = [o for o in outs if o[0]]
    pages = sorted(o[1] for o in outs)
    line = (f"{label:20} {len(outs):4} sampled | cut {len(cut):3} ({100 * len(cut) / len(outs):.0f}%)"
            f" | page median {statistics.median(pages):.0f} tok, p90 {pages[int(.9 * len(pages))]}")
    if cut:
        first = statistics.median(o[1] for o in cut)
        rest = statistics.median(o[2] - o[1] for o in cut)
        line += (f" | cut ones: page {first:.0f}, rest {rest:.0f} tok (re-call re-reads the page;"
                 f" {sum(o[1] for o in cut)} tok over the sample)")
    print(line)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--db", required=True)
    ap.add_argument("--n", type=int, default=300)
    ap.add_argument("--seed", type=int, default=7)
    args = ap.parse_args()
    store = Store(Path(args.db))
    rng = random.Random(args.seed)
    rows = store.symbols_of_lang("csharp", TYPE_KINDS + ("method",))
    types = sorted({r.name for r in rows if r.kind in TYPE_KINDS})
    methods = sorted({(r.name, r.path, r.line) for r in rows if r.kind == "method"})
    spans: dict[str, list] = {}
    for r in rows:
        if r.kind in TYPE_KINDS:
            spans.setdefault(r.path, []).append(r)

    def sample(items, n):
        return rng.sample(items, min(n, len(items)))

    def refs(name):
        a = refsview.render_refs(store, name)
        return ("(raise the limit" in a, tokens(a),
                tokens(refsview.render_refs(store, name, limit=UNLIMITED)))

    report("refs Type", [refs(n) for n in sample(types, args.n)])
    report("refs method", [refs(n) for n, _, _ in sample(methods, args.n)])
    outs = []
    for name, path, line in sample(methods, args.n):
        enc = [t for t in spans.get(path, []) if t.line <= line <= t.end_line]
        if enc:
            target = f"{max(enc, key=lambda t: t.line).name}.{name}"
            a = changeview.render_change(store, target)
            outs.append(("(raise the limit)" in a, tokens(a),
                         tokens(changeview.render_change(store, target, limit=UNLIMITED))))
    report("change Type.Member", outs)
    outs = []
    for path in sample(sorted({r.path for r in rows}), args.n // 3):
        a = impact.generate_impact(store, [path])
        outs.append(("truncated by budget" in a, tokens(a),
                     tokens(impact.generate_impact(store, [path], budget=UNLIMITED))))
    report("impact FILE", outs)
    store.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
