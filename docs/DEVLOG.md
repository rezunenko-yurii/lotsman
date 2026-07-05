# Development log

Condensed history of how Lotsman was built and verified. Each stage ended in a
working, tested state.

## v1 — pipeline (stages 0–5)

- Project skeleton; token estimation and identifier subtokenization.
- `scanner` (git ls-files / walk, language map, size filters), `store`
  (SQLite: files / symbols / idents, incremental upsert by sha256),
  `extract` (tree-sitter definition queries for 12 languages + regex
  fallback), `indexer` (incremental pass).
- `graph`: reference graph + personalized PageRank (pure-Python power
  iteration); rank distribution down to definitions.
- `repomap`: greedy token-budgeted selection; `--focus` files excluded from
  output (they're already in the agent's context).
- `search`: Okapi BM25 over symbol documents; exact-name boost.
- CLI: `index / map / search / outline / defs / refs / stats`, `--json`.
- Verified: unit + e2e tests on a fixture repo; self-index; field test on
  Django (2361 files: cold index ~4 s, no-op ~0.13 s).

## v2 — precision and semantics (stages 6–8)

- **Use-site references**: custom tree-sitter queries (calls, instantiations,
  decorators, inheritance, imports) replace lexical counting; a parameter
  named `request` no longer counts as a reference. Lexical fallback retained.
- Ranking noise control: IDF per identifier, ambient-vocabulary cutoff
  (>25% of files), builtin-method stopwords. Django map baseline established:
  `cached_property` / `ValidationError` / `ForeignKey` at the top, zero
  generic names.
- **Local embeddings** (model2vec potion-base-8M) stored as BLOBs in the same
  SQLite; **hybrid search** via Reciprocal Rank Fusion; graceful BM25-only
  degradation. Test-path demotion, duplicate-signature collapse.
- git repo initialized; measured: full index + embedding of 43.5k symbols in
  ~6 s total.

## v3 — agent integration (stages 9–13)

- **Rank cache** keyed by an index-state digest: warm `map` 1.3 s → 0.11 s.
- **MCP stdio server** on the stdlib (initialize / tools/list / tools/call);
  tools `map / search / outline / defs / refs / impact` with typed schemas;
  throttled auto-reindex; verified against real Claude Code 2.1.150
  (`✓ Connected`, protocol echo, clean unknown-method rejection).
- **Battleground**: Unity project SDK-Kitchen-V3 (2008 C# files, Plastic SCM).
  Drove three features: precise C# references (calls, `new`, inheritance,
  attributes, generic/field/parameter types), `.lotsmanignore` for vendored
  SDKs, automatic minified/generated-file filtering.
- **Impact map** (`impact`): changed files (explicit / git status / mtime
  window) + dependents ranked by usage.
- **Session-start hook**: repo map auto-injected into agent context (~0.3 s).
- **Live behavioral test passed**: agent answered an impact question with
  4 tool calls and one 15-line read; measured scenario economy 24×.
- Renamed codemap → lotsman (PyPI name taken; top GitHub codemap is a
  same-niche tool). Packaged 1.0.0: MIT, CHANGELOG, CI, clean-venv install
  verified.

## v1.1 — trust hardening (stage 14)

Response to external review (all factual claims verified against the code
before acting):

- Maturity claim lowered to **Beta**; `impact` repositioned as a *heuristic*
  impact map in every user-facing description.
- **Language regression fixtures**: C# (Unity idioms — properties,
  constructors, attributes, inheritance), TypeScript, Go for both definitions
  and use-site references.
- **`index --verify`**: full re-hash pass that catches content changes
  preserving both mtime and size; test fabricates exactly that case.
- **Reproducible benchmark harness** (`benchmarks/bench_django.py`, Django
  pinned at tag 5.2) + `docs/BENCHMARKS.md`; the 24× figure is now a script,
  not an anecdote.
- **`lotsman doctor`**: per-language grammar status, embeddings availability,
  index freshness/version, ignore rules, change-detection mode.
- Docs translated to English; README redesigned (badges, mermaid pipeline,
  real command outputs, honest-limitations section).

Declined from the review, with reasons: MCP SDK adoption (hand-rolled subset
is a deliberate zero-infrastructure choice, empirically verified against the
real client; revisit if a client breaks), mtime_ns/inode tracking (float
mtime already gives ~µs resolution; `--verify` addresses the actual risk),
test-file split (deferred to v1.2).

## v1.2 — quality gates (stage 15)

Response to the second review round:

- `doctor --json` + `--fail-on-warn`: machine-readable health for agents/CI.
  Declined the per-policy flag zoo (`--fail-on-fallback-language X` etc.) —
  the JSON output lets any policy be expressed externally.
- Benchmark harness now asserts quality, not just speed: map-baseline symbols,
  no-generic-tops regex, and top-5 hit rate on four navigation questions;
  non-zero exit on regression. Found and fixed a real gap while calibrating:
  the harness didn't embed, so gates ran in BM25 mode while the CLI defaults
  to hybrid.
- Confidence markers moved into command *output* (`impact` header, `refs`
  label) — agents read output, not READMEs.
- MCP protocol fixtures: malformed JSON recovery, truncation, stdout purity,
  pinned tool schemas.
- Test monolith split into 10 layered files (56 tests) — the reviewer raised
  it twice; twice is a signal, not a taste.
- Declined `doctor --sample` (result-quality inspection belongs to the
  benchmark quality gates, not another doctor mode).
