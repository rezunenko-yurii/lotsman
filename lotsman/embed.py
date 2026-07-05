"""Local static embeddings via model2vec (no torch, no API keys).

Degrades gracefully: if model2vec or the model is unavailable, `available()`
returns False and search falls back to pure BM25.
"""

from __future__ import annotations

import os
from functools import lru_cache

from lotsman.store import Store, SymbolRow

DEFAULT_MODEL = "minishlab/potion-base-8M"
BATCH_SIZE = 4096


@lru_cache(maxsize=1)
def _load_model():
    try:
        from model2vec import StaticModel
        return StaticModel.from_pretrained(
            os.environ.get("LOTSMAN_EMBED_MODEL", DEFAULT_MODEL))
    except Exception:
        return None


def available() -> bool:
    return _load_model() is not None


def symbol_text(sym: SymbolRow) -> str:
    """Text representation of a symbol for embedding: kind, name, signature,
    and path words all carry meaning."""
    path_words = sym.path.replace("/", " ").replace("_", " ").replace(".", " ")
    return f"{sym.kind} {sym.name}: {sym.signature} | {path_words}"


def encode(texts: list[str]):
    """L2-normalized float32 vectors, or None if the model is unavailable."""
    model = _load_model()
    if model is None:
        return None
    import numpy as np
    vecs = model.encode(texts).astype(np.float32)
    norms = np.linalg.norm(vecs, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return vecs / norms


def embed_missing(store: Store) -> int:
    """Embed symbols whose vector is NULL (new/changed files). Returns count."""
    if not available():
        return 0
    import numpy as np  # noqa: F401  (guarded by available())
    total = 0
    while True:
        rows = store.conn.execute(
            "SELECT s.id, f.path, s.name, s.kind, s.line, s.end_line, s.signature "
            "FROM symbols s JOIN files f ON f.id = s.file_id "
            "WHERE s.vector IS NULL LIMIT ?", (BATCH_SIZE,)).fetchall()
        if not rows:
            break
        texts = [symbol_text(SymbolRow(path=r[1], name=r[2], kind=r[3],
                                       line=r[4], end_line=r[5], signature=r[6]))
                 for r in rows]
        vecs = encode(texts)
        store.conn.executemany(
            "UPDATE symbols SET vector=? WHERE id=?",
            [(vecs[i].tobytes(), rows[i][0]) for i in range(len(rows))])
        store.commit()
        total += len(rows)
    return total
