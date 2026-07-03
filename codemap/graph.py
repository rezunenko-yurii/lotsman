"""Reference graph + personalized PageRank over files, rank distribution to
definitions. Pure-Python power iteration — no networkx."""

from __future__ import annotations

import math
from collections import Counter, defaultdict

from codemap.textutil import is_well_named

DAMPING = 0.85
MAX_ITER = 60
TOL = 1e-8
# An identifier mentioned in this fraction of all files is ambient vocabulary
# (`request` in a web framework), not a navigation signal — lexical reference
# counting cannot tell a parameter named `request` from a call to `request()`.
# Meaningless on small repos where any name easily spans 25% of files.
MAX_REF_FRACTION = 0.25
MIN_FILES_FOR_REF_CUTOFF = 20


def _ident_multiplier(ident: str, mentions: set[str]) -> float:
    mul = 1.0
    if ident in mentions:
        mul *= 10.0
    if is_well_named(ident):
        mul *= 2.0
    if ident.startswith("_"):
        mul *= 0.1
    return mul


def build_edges(
    definitions: dict[str, set[str]],
    references: dict[str, Counter],
    mentions: set[str] | None = None,
) -> dict[str, dict[str, list[tuple[str, float]]]]:
    """edges[src][dst] = [(ident, weight), ...]; src references ident defined in dst.

    Weight = mul(ident) * idf(ident) * sqrt(count) / n_definers:
    - idf penalizes names referenced by most of the repo (generic `value`,
      `request`, ... carry no navigation signal — lexical counting is noisy,
      so this matters more than in AST-reference schemes);
    - splitting across definers penalizes names defined in many places.
    """
    mentions = mentions or set()
    n_files = max(len(references), 1)
    ref_file_count: Counter = Counter()
    for counts in references.values():
        ref_file_count.update(counts.keys())

    edges: dict[str, dict[str, list[tuple[str, float]]]] = defaultdict(
        lambda: defaultdict(list))
    for src, counts in references.items():
        for ident, num in counts.items():
            definers = definitions.get(ident)
            if not definers:
                continue
            rf = ref_file_count[ident]
            if (n_files >= MIN_FILES_FOR_REF_CUTOFF
                    and rf / n_files > MAX_REF_FRACTION
                    and ident not in mentions):
                continue
            idf = math.log(1 + n_files / (1 + rf))
            mul = _ident_multiplier(ident, mentions)
            weight = mul * idf * math.sqrt(num) / len(definers)
            for dst in definers:
                if dst == src:
                    continue
                edges[src][dst].append((ident, weight))
    return edges


def pagerank(
    nodes: set[str],
    edges: dict[str, dict[str, list[tuple[str, float]]]],
    personalization: dict[str, float] | None = None,
) -> dict[str, float]:
    if not nodes:
        return {}
    n = len(nodes)
    if personalization:
        total = sum(personalization.values())
        p = {node: personalization.get(node, 0.0) / total for node in nodes}
    else:
        p = {node: 1.0 / n for node in nodes}

    out_weight = {
        src: sum(w for dsts in dst_map.values() for _, w in dsts)
        for src, dst_map in edges.items()
    }
    rank = dict(p)
    for _ in range(MAX_ITER):
        new_rank = {node: 0.0 for node in nodes}
        dangling = 0.0
        for node in nodes:
            r = rank[node]
            ow = out_weight.get(node, 0.0)
            if ow <= 0:
                dangling += r
                continue
            for dst, pairs in edges[node].items():
                w = sum(x for _, x in pairs)
                new_rank[dst] += r * (w / ow)
        delta = 0.0
        for node in nodes:
            val = (1 - DAMPING) * p[node] + DAMPING * (new_rank[node] + dangling * p[node])
            delta += abs(val - rank[node])
            rank[node] = val
        if delta < TOL * n:
            break
    return rank


def rank_definitions(
    rank: dict[str, float],
    edges: dict[str, dict[str, list[tuple[str, float]]]],
) -> dict[tuple[str, str], float]:
    """Distribute each file's rank across incoming (file, ident) definitions."""
    out_weight = {
        src: sum(w for dsts in dst_map.values() for _, w in dsts)
        for src, dst_map in edges.items()
    }
    def_rank: dict[tuple[str, str], float] = defaultdict(float)
    for src, dst_map in edges.items():
        r = rank.get(src, 0.0)
        ow = out_weight.get(src, 0.0)
        if r <= 0 or ow <= 0:
            continue
        for dst, pairs in dst_map.items():
            for ident, w in pairs:
                def_rank[(dst, ident)] += r * (w / ow)
    return dict(def_rank)
