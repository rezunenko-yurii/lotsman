# Lotsman — design

## The problem

An AI agent working on a large codebase spends most of its tokens not on
generating code but on **navigation**. Four cost centers:

1. **Search** — "where does this live?" (grep chains, tree walking).
2. **Reading** — whole files loaded into context for a couple of lines.
3. **Context bloat** — everything read stays in the window and is paid for on
   every subsequent step.
4. **Wandering** — the agent re-finds things it already found.

## The solution

Lotsman is a local codebase indexer with CLI and MCP interfaces, designed as a
*sense organ* for an AI agent. The core principle is **amortization**: compute
the expensive thing once (the index), query it cheaply many times (sub-second
answers, tens of lines instead of thousands).

| Command | Question it answers | Replaces |
|---|---|---|
| `map` | "how is this project structured; what matters?" | reading dozens of files |
| `search` | "where is the code that does X?" | a grep-and-read cascade |
| `outline` | "what's inside this file?" | reading the file whole |
| `refs` / `defs` | "who uses / where is symbol X?" | repo-wide grep + reads |
| `impact` | "what changed and what depends on it?" | broken tests as discovery |

## Algorithmic core

1. **Incremental index** (sha256 + mtime fast path): re-runs touch only
   changed files. Storage is SQLite — zero infrastructure. An optional
   `--verify` pass re-hashes everything, catching the theoretical case of a
   change that preserves both size and timestamp. Index-format changes bump
   `INDEX_VERSION` and trigger an automatic full rebuild.
2. **Symbol extraction via tree-sitter**: precise definitions (functions,
   classes, methods, types, properties) with signatures and line ranges for
   12 languages; regex fallback for the rest.
3. **Use-site references via tree-sitter**: only calls, instantiations,
   inheritance, attributes/decorators, and type usages count as references
   (11 languages; lexical fallback otherwise). This is the key precision move:
   a parameter named `request` is not a reference to a `request()` method.
4. **Reference graph + personalized PageRank** (pure-Python power iteration).
   Files are nodes; "file A uses a name defined in B" is an edge weighted by
   `boost(name) × IDF(name) × √count / definers`. Names referenced by more
   than 25% of files are dropped as ambient vocabulary — with lexical noise
   this matters more than in AST-perfect schemes. Personalization biases
   ranks toward the agent's current focus files and mentioned identifiers.
5. **Token-budgeted repo map**: greedy selection of top-ranked definitions
   until the budget is spent. The agent gets the project's skeleton at a fixed,
   predictable cost. The default (non-personalized) ranking is cached per
   index-state digest, making warm maps ~20× faster.
6. **Hybrid search**: Okapi BM25 over symbol documents (camelCase/snake_case
   subtokens + signature + path) fused with cosine similarity over local
   static embeddings (model2vec, ~30 MB, no torch, no API keys) via
   Reciprocal Rank Fusion (k=60). Test paths are demoted; duplicate
   signatures collapse. Without the embeddings extra, search degrades to
   pure BM25 transparently.
7. **Heuristic impact map**: changed files (explicit list → `git status` →
   mtime window for non-git repos like Plastic SCM) plus the files that
   reference their symbols by name, ranked by usage.

## Design principles

- **Zero infrastructure.** stdlib + tree-sitter (+ optional model2vec). No
  vector databases, no API keys, no background daemons.
- **Query speed over index speed.** Indexing is an amortized one-time cost;
  queries must feel instant.
- **Output is for LLMs.** Compact, deterministic, `path:line` addressed;
  `--json` for machine consumption; every report is token-budgeted.
- **Degrade, don't fail.** Missing grammar → regex; missing model → BM25;
  no git → walk. `lotsman doctor` makes every degradation visible.
- **Honest contract.** Lotsman is a cheap local navigator, not a semantic
  code-intelligence engine. `refs`/`impact` are name-based heuristics, not
  compiler-grade analysis, and the docs say so.

## Quality baseline (regression guard)

Map quality is checked against a Django clone (see `benchmarks/`): the top of
the default map must surface `cached_property`, `ValidationError`,
`ForeignKey` — genuinely central Django internals. Generic names (`value`,
`list`, `request`) appearing at the top indicate a ranking regression.

## Out of scope (for now)

- Type resolution / LSP-grade references (revisit when the heuristic ceiling
  is actually hit).
- LLM-generated hierarchical summaries (RAPTOR/GraphRAG style).
- Cross-repository indexes, file watchers, long-running daemons.

## Evolution

- **v1** — pipeline: incremental index, PageRank map, BM25 search, CLI.
- **v2** — precision: use-site references, IDF + ambient-vocabulary cutoff,
  local embeddings with RRF hybrid search.
- **v3** — agent integration: rank cache, stdlib MCP server, session-start
  map injection, heuristic impact map; verified end-to-end in a live agent
  session on a 2008-file Unity project.
- **v1.1** — trust hardening after external review: Beta status, language
  regression fixtures (C#/TS/Go), `--verify` mode, reproducible benchmark
  harness, `doctor`, English docs.
