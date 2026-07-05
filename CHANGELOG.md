# Changelog

## 1.3.0 — 2026-07-05

Release-state guarantees, in response to the fourth review round.

- **Release consistency test** (`tests/test_release.py`): pyproject version,
  `lotsman.__version__` and the CHANGELOG top entry must match; the Beta
  classifier is pinned by a test so raising maturity is a deliberate act.
- **`lotsman --version`** flag.
- **Machine-readable confidence**: `refs --json` now carries a `confidence`
  block (`heuristic` / `name-based` / `type_resolution: false` / known blind
  spots) — the JSON contract is as honest as the text output.
- **Install-path CI job**: builds the wheel, installs it, and smoke-tests the
  CLI outside the repo checkout — packaging regressions can't hide behind
  `pip install -e`.
- **MCP compatibility note** in README: which client is actually verified
  (Claude Code 2.1.150) and what to do when another client breaks.
- CI fix (shipped between releases): the workflow file was invalid YAML from
  day one (unquoted colon in a step name) and had never actually run; all six
  matrix configurations are green as of this release.

## 1.2.0 — 2026-07-05

Quality gates and machine-readable health, in response to the second external
review round.

- **`lotsman doctor --json`** — machine-readable health report for agents/CI;
  **`--fail-on-warn`** turns warnings (stale index, missing embeddings,
  grammar fallbacks) into a non-zero exit. Any policy can be built on the JSON.
- **Quality gates in the benchmark harness**: `benchmarks/bench_django.py` now
  asserts map content (must contain `cached_property` / `ValidationError` /
  `ForeignKey`, must not be topped by generic names) and top-k hit rate for
  four navigation questions; a failure exits non-zero. The Django baseline is
  now executable, not just documented. The harness also embeds by default,
  matching CLI behavior — gates run in hybrid-search mode.
- **Confidence markers in command output** (not only in docs — agents read
  output): `impact` starts with a heuristic/name-based disclaimer; `refs`
  labels its reference list as name-based matching without type resolution.
- **MCP protocol fixtures**: malformed-JSON recovery, output truncation,
  stdout purity (every line must parse as JSON-RPC), tool-schema stability
  (required arguments are pinned by test).
- **Test suite split by layer**: `test_extract / test_scanner / test_graph /
  test_indexing / test_repomap / test_search / test_impact / test_doctor /
  test_mcp / test_cli` (56 tests).
- README fully English (the tagline no longer needs a dictionary).

## 1.1.0 — 2026-07-05

Trust hardening after external review, English docs, visual polish.

- **Honest maturity**: classifier lowered to `4 - Beta`; `impact` is now
  described everywhere as a *heuristic* impact map (name-based matching, no
  type resolution) — a navigation aid, not a compiler-grade dependency graph.
- **`lotsman index --verify`**: full re-hash pass bypassing the mtime+size
  fast path; catches content changes that preserve both timestamp and size.
- **`lotsman doctor`**: environment and index health check — per-language
  grammar status (tree-sitter vs fallback), embeddings availability, index
  freshness and version, rank-cache state, ignore rules, change-detection mode.
- **Reproducible benchmarks**: `benchmarks/bench_django.py` (Django pinned at
  tag 5.2) reproduces every performance number and the 24× token-savings
  scenario; results and methodology in `docs/BENCHMARKS.md`.
- **Language regression fixtures**: C# (Unity idioms: properties, constructors,
  attributes, inheritance), TypeScript and Go extraction tests for both
  definitions and use-site references (47 tests total).
- **Docs in English**: README redesigned (badges, mermaid pipeline diagram,
  real command outputs, honest-limitations section); design rationale moved to
  `docs/DESIGN.md`, development history to `docs/DEVLOG.md`.

## 1.0.0 — 2026-07-05

First packaged release (as `codemap`; renamed to `lotsman` the same day —
the codemap namespace was taken on both PyPI and GitHub). Verified end-to-end:
a live Claude Code session on a Unity project (2008 C# files) answered an
impact question with 4 tool calls and a single 15-line file read.

### Indexing
- Incremental SQLite index (sha256 + mtime fast path); Django (2361 files)
  cold ~4 s, no-op ~0.13 s.
- Definitions via tree-sitter for 12 languages (python, js, ts, tsx, go, rust,
  java, c, cpp, ruby, csharp, php) + regex fallback.
- Use-site references (calls, inheritance, attributes, types) for 11
  languages; lexical fallback elsewhere.
- `.lotsmanignore` (gitignore-lite) for vendored code; automatic
  minified/generated-file filter; index versioning with automatic full rebuild.

### Ranking and map
- Reference graph with IDF weighting and ambient-vocabulary cutoff;
  personalized PageRank (dependency-free power iteration).
- Token-budgeted repo map; `--focus` / `--mention` personalization.
- Rank cache keyed by index-state digest: warm map ~0.1 s.

### Search
- BM25 over symbols with camelCase/snake_case subtokenization.
- Optional local embeddings (model2vec, no torch, no API keys).
- Hybrid mode via Reciprocal Rank Fusion; signature dedupe, test-path
  demotion; graceful BM25-only degradation.

### Impact
- `lotsman impact`: changed files + dependents ranked by usage; change
  detection: explicit list / git status / mtime window.

### Agent integration
- MCP stdio server on the stdlib (map / search / outline / defs / refs /
  impact), verified against Claude Code 2.1.150.
- CLAUDE.md navigation policy; SessionStart map-injection hook; `--json`
  output for machine consumption.

Measured effect: agent navigation scenario ~2.8k tokens instead of ~67k (24×).
