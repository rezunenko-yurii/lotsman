"""Symbol search: BM25 (Okapi), vector (model2vec cosine), and hybrid via
Reciprocal Rank Fusion. No embeddings available -> transparent BM25 fallback."""

from __future__ import annotations

import math
from collections import Counter, defaultdict
from dataclasses import dataclass

from lotsman import embed
from lotsman.store import Store, SymbolRow
from lotsman.textutil import is_test_path, split_ident, tokenize

K1 = 1.5
B = 0.75
EXACT_NAME_BOOST = 2.0
RRF_K = 60
CANDIDATE_POOL = 50  # per-ranker depth fed into RRF


@dataclass
class Hit:
    score: float
    symbol: SymbolRow


def _symbol_doc(sym: SymbolRow) -> list[str]:
    tokens = [sym.name.lower(), sym.kind]
    tokens += split_ident(sym.name)
    tokens += tokenize(sym.signature)
    tokens += tokenize(sym.path.replace("/", " ").replace(".", " "))
    return tokens


def search_bm25(store: Store, query: str, limit: int = 10) -> list[Hit]:
    q_tokens = tokenize(query) or [t.lower() for t in query.split()]
    if not q_tokens:
        return []
    symbols = store.all_symbols()
    if not symbols:
        return []

    docs = [_symbol_doc(s) for s in symbols]
    doc_len = [len(d) for d in docs]
    avg_len = sum(doc_len) / len(docs)

    # Document frequencies for query terms only (cheap).
    q_set = set(q_tokens)
    df: Counter = Counter()
    postings: dict[str, dict[int, int]] = defaultdict(dict)
    for i, d in enumerate(docs):
        counts = Counter(d)
        for term in q_set:
            if term in counts:
                df[term] += 1
                postings[term][i] = counts[term]

    n = len(docs)
    scores: dict[int, float] = defaultdict(float)
    for term in q_tokens:
        if term not in postings:
            continue
        idf = math.log(1 + (n - df[term] + 0.5) / (df[term] + 0.5))
        for i, tf in postings[term].items():
            denom = tf + K1 * (1 - B + B * doc_len[i] / avg_len)
            scores[i] += idf * (tf * (K1 + 1)) / denom

    q_joined = "".join(q_tokens)
    hits = []
    for i, score in scores.items():
        sym = symbols[i]
        name_low = sym.name.lower()
        if name_low == q_joined or name_low in q_set:
            score *= EXACT_NAME_BOOST
        hits.append(Hit(score, sym))
    hits.sort(key=lambda h: h.score, reverse=True)
    return hits[:limit]


def search_vector(store: Store, query: str, limit: int = 10) -> list[Hit]:
    """Cosine similarity over stored symbol vectors. Empty if unavailable."""
    q_vec = embed.encode([query])
    if q_vec is None:
        return []
    rows = store.symbols_with_vectors()
    if not rows:
        return []
    import numpy as np
    mat = np.frombuffer(b"".join(v for _, v in rows), dtype=np.float32)
    mat = mat.reshape(len(rows), -1)
    sims = mat @ q_vec[0]
    top = np.argsort(-sims)[:limit]
    return [Hit(float(sims[i]), rows[i][0]) for i in top]


def _rrf(ranked_lists: list[list[Hit]], limit: int) -> list[Hit]:
    """Reciprocal Rank Fusion: score = sum over lists of 1/(RRF_K + rank)."""
    fused: dict[tuple[str, int], float] = defaultdict(float)
    best: dict[tuple[str, int], Hit] = {}
    for hits in ranked_lists:
        for rank, hit in enumerate(hits):
            key = (hit.symbol.path, hit.symbol.line)
            fused[key] += 1.0 / (RRF_K + rank + 1)
            best.setdefault(key, hit)
    result = [Hit(score, best[key].symbol) for key, score in fused.items()]
    result.sort(key=lambda h: h.score, reverse=True)
    return result[:limit]


TEST_PATH_DEMOTION = 0.6
_is_test_path = is_test_path


def _polish(hits: list[Hit], limit: int) -> list[Hit]:
    """Demote test files and collapse duplicate (name, kind, signature) hits —
    N identical mocks must not crowd out distinct results."""
    for h in hits:
        if _is_test_path(h.symbol.path):
            h.score *= TEST_PATH_DEMOTION
    hits.sort(key=lambda h: h.score, reverse=True)
    seen: set[tuple[str, str, str]] = set()
    result = []
    for h in hits:
        key = (h.symbol.name, h.symbol.kind, h.symbol.signature)
        if key in seen:
            continue
        seen.add(key)
        result.append(h)
        if len(result) == limit:
            break
    return result


def search(store: Store, query: str, limit: int = 10, mode: str = "auto") -> list[Hit]:
    """mode: auto | hybrid | bm25 | vector. `auto` = hybrid when vectors exist."""
    if mode == "auto":
        mode = "hybrid" if (embed.available() and store.vector_count() > 0) else "bm25"
    if mode == "bm25":
        hits = search_bm25(store, query, CANDIDATE_POOL)
    elif mode == "vector":
        hits = search_vector(store, query, CANDIDATE_POOL)
    elif mode == "hybrid":
        bm25_hits = search_bm25(store, query, CANDIDATE_POOL)
        vec_hits = search_vector(store, query, CANDIDATE_POOL)
        hits = _rrf([bm25_hits, vec_hits], CANDIDATE_POOL) if vec_hits else bm25_hits
    else:
        raise ValueError(f"unknown search mode: {mode}")
    return _polish(hits, limit)
