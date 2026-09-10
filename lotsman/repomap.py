"""Token-budgeted repo map: greedy selection of top-ranked definitions."""

from __future__ import annotations

from collections import defaultdict

from lotsman import graph
from lotsman.store import Store, SymbolRow
from lotsman.textutil import estimate_tokens

DEFAULT_BUDGET = 2048
RANK_CACHE_SIZE = 5000  # top definitions kept; enough for any realistic budget


def generate_map(
    store: Store,
    budget: int = DEFAULT_BUDGET,
    focus: set[str] | None = None,
    mentions: set[str] | None = None,
) -> str:
    """Render the most important definitions across the repo within a token budget.

    focus: files already in the agent's context — they bias ranking toward their
    dependencies but are excluded from output.
    """
    focus = focus or set()
    mentions = mentions or set()

    # Default (non-personalized) ranking is deterministic for a given index
    # state — serve it from cache. Personalized requests always recompute.
    personalized = bool(focus or mentions)
    ranked: list[tuple[str, str, float]] | None = None
    stamp = None
    if not personalized:
        stamp = store.state_stamp()
        ranked = store.load_rank_cache(stamp)

    valid_focus: set[str] = set()
    if ranked is None:
        definitions = store.definitions()
        references = store.references()
        nodes = set(references.keys())
        for definers in definitions.values():
            nodes.update(definers)
        if not nodes:
            return "(empty index — run `lotsman index` first)"

        owners = store.member_owners(graph.OWNER_GATED_SUFFIXES)
        edges = graph.build_edges(definitions, references, mentions, owners)
        valid_focus = {f for f in focus if f in nodes}
        personalization = _personalization(valid_focus, mentions,
                                           definitions, references)
        rank = graph.pagerank(nodes, edges, personalization)
        def_ranks = graph.rank_definitions(rank, edges)
        ranked = sorted(
            ((path, ident, score) for (path, ident), score in def_ranks.items()),
            key=lambda x: x[2], reverse=True)
        if not personalized and stamp is not None:
            store.save_rank_cache(stamp, ranked[:RANK_CACHE_SIZE])

    # (file, ident) -> symbol rows, to render actual signatures.
    symbols_by_key: dict[tuple[str, str], list[SymbolRow]] = defaultdict(list)
    for sym in store.all_symbols():
        symbols_by_key[(sym.path, sym.name)].append(sym)

    # Greedy selection under budget with incremental cost accounting.
    selected: dict[str, list[SymbolRow]] = defaultdict(list)
    file_rank: dict[str, float] = defaultdict(float)
    used_tokens = 0
    seen_lines: set[tuple[str, int]] = set()
    for path, ident, score in ranked:
        if path in valid_focus:
            continue
        rows = symbols_by_key.get((path, ident))
        if not rows:
            continue
        for row in rows[:3]:  # cap overload spam per ident
            if (path, row.line) in seen_lines:
                continue
            cost = estimate_tokens(f"{row.line:5}: {row.signature}\n")
            if path not in selected:
                cost += estimate_tokens(f"\n{path}:\n")
            if used_tokens + cost > budget:
                return _render(selected, file_rank)
            selected[path].append(row)
            file_rank[path] += score
            seen_lines.add((path, row.line))
            used_tokens += cost
    return _render(selected, file_rank)


MENTION_REFERRER_WEIGHT = 0.3


def _personalization(focus: set[str], mentions: set[str],
                     definitions: dict[str, set[str]],
                     references: dict) -> dict[str, float] | None:
    """Random jumps land on the task's neighborhood: focus files, files
    defining a mentioned name and, weaker, files using it. Without this a
    mention only nudged edges into one file and the rest of the map stayed
    the global top (measured: 3 of 44 files relevant for `WindowsManager`)."""
    weights: dict[str, float] = {f: 1.0 for f in focus}
    for ident in mentions:
        for path in definitions.get(ident, ()):
            weights[path] = max(weights.get(path, 0.0), 1.0)
        for path, counts in references.items():
            if ident in counts:
                weights[path] = max(weights.get(path, 0.0),
                                    MENTION_REFERRER_WEIGHT)
    return weights or None


def _render(selected: dict[str, list[SymbolRow]], file_rank: dict[str, float]) -> str:
    if not selected:
        return "(no ranked definitions — repo may be too small or unreferenced)"
    parts: list[str] = []
    for path in sorted(selected, key=lambda p: file_rank[p], reverse=True):
        rows = sorted(selected[path], key=lambda r: r.line)
        parts.append(f"{path}:")
        for row in rows:
            parts.append(f"{row.line:5}: {row.signature}")
        parts.append("")
    return "\n".join(parts).rstrip() + "\n"
