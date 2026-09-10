# Changelog

## Unreleased

- **Caller check after a signature edit (C#, in `impact`).** The index keeps
  each method's parameter shape (required, optional, `params`, extension
  `this`) and remembers the shape it saw before an edit (24 h; reverting the
  edit forgets it). `impact` then counts the arguments at every call site
  `change` would list and puts a "Signature changes" section first, never cut
  by the budget: `breaks` — no overload takes that count — and `maybe` —
  compiles only through optional/`params` parameters — with `file:line`;
  unparsed calls are `unclear`, not fine. Same-name calls of other types are
  skipped and counted (receiver declared as another type, a static call on
  another type, a bare call inside a type with its own method, or an argument
  count the old version never took). On a copy of a Unity project a seeded
  `AddSink(sink)` → `AddSink(sink, priority)` lists all 5 real callers
  (4 files) and none of the same-name `PurchasingFeedbackPresenter.AddSink`
  calls; before, the 4 files sat among 208 impacted ones, `AddSink` named in
  2, no lines, nothing saying the build breaks. +6% cold index time. Index
  format 10.
- **`lotsman unity errors` — compile errors from the Editor log.** Reads the
  last compilation only (earlier errors may be fixed), dedupes repeats,
  counts warnings apart, lists `file:line:col CODE message` relative to the
  index, and flags staleness: files edited after the log was written are
  named (files with errors first) and marked on their error lines — "these
  errors may predate those edits". Finds the log in the project's
  `Logs/Editor.log` or the OS location (macOS, Windows, Linux), prefers the
  one whose `-projectpath` is this project, refuses to report another
  project's log; `--log PATH` for batchmode `-logFile`s. Only compiler lines
  and the project path are read — the log header carries access tokens.
- **`#if` and define symbols (Unity).** C# `#if/#elif/#else` branches are
  indexed with their effective condition (`#else` of `#if A` is `!A`, nesting
  ANDed). `outline` marks symbols under a condition (`[#if UNITY_IOS]`, only
  where it differs from the enclosing symbol's: +0.5% outline size on a Unity
  project). `unity FILE|Type` adds the targets the assembly builds for
  (asmdef platforms and `defineConstraints`), each condition with its lines
  and where it compiles (`UNITY_IOS && !UNITY_EDITOR → iOS only`), symbols
  defined nowhere, and where project defines come from (Player Settings
  `scriptingDefineSymbols` with line numbers, `csc.rsp`, asmdef
  `versionDefines` checked against Packages). Three-valued: what files cannot
  tell (the Editor's active target without a generated csproj, Unity's
  `ENABLE_*` switches) is `?`, not a guess. Conditions agree with
  tree-sitter's preprocessor nodes on 360/360 symbols. Index format 9.
- **Unity assemblies.** `.asmdef`/`.asmref` files are indexed (GUIDs from the
  `.meta` next to them). `lotsman unity FILE|Type|Assembly` (CLI and MCP tool)
  names the assembly that compiles the code — nearest asmdef, or Unity's
  predefined `Assembly-CSharp`/`-firstpass`/`-Editor` rules — its references
  with `asmdef:line` (GUID references resolved), who references it directly
  (references are not transitive) and which predefined assemblies see it.
  `unity FILE USES` answers "does this compile if I use USES here?" and says
  which asmdef line to edit. `outline` of a C# file gets an `assembly:` line;
  `change` marks callers whose assembly cannot see the definition's. On a
  Unity project (24 asmdefs) file → assembly matches Unity's generated
  `.csproj` for 1297/1297 files and the reference graph for 27/27 assemblies
  (`benchmarks/check_unity_csproj.py`). Index format 8.
- **JSON configs reference code.** `.json` (and `.rc` files that contain
  JSON) are indexed: `"$type": "Ns.Type"`, keys named after members
  (`"p_Checker"` → `Checker`, `f_`/`t_` prefixes, `-------LOCKED-------`
  markers) and PascalCase string values (`"$merge": "Union"`,
  `["t_CrmConnector"]`) count as references with lines. `refs` shows them in a
  `data —` block, `change` lists configs that break on a rename (marking the
  ones that never name the type), `impact` counts them, `outline file.json`
  shows the keys. Big JSON (> 256 KB), generated one-liners and non-JSON
  `.rc` are skipped; data files never feed the map ranking. On a Unity
  project: config questions 54k → 4k tokens; every JSON mention of a C# type
  found (159/159). Index format 7.
- **`lotsman change TARGET`** (CLI and MCP tool) — before changing a symbol,
  one capped report: its definition, declarations it implements in
  supertypes, overrides/implementations in subtypes (test doubles included),
  and callers `file:line` in files that name its type family, tests marked.
  Same-name uses on unrelated types are counted, not listed; extension
  methods are not gated; `--json` for tools. Works on `Type.Member`, `Member`
  (owner inferred when unique) and `Type`. On a Unity project's change
  questions: 18% of the grep path's tokens, 0–2 unrelated files where
  `refs` + `impact` showed 29–44. C# inheritance comes from `base_list`
  (generic, qualified and record bases); index format 6.
- **Markdown docs are indexed.** Headings become sections: `outline file.md`
  shows the heading tree with line ranges, and `search` adds a `docs:` block
  after the code hits — the section's own range plus a one-line snippet, so an
  agent reads 20 lines instead of a 600-line guide. Russian and English text
  (Snowball Russian stemming, `ё`→`е`, identifier subwords), SQLite FTS5;
  `search --docs N` (default 3, 0 = none). Docs never displace code hits and
  stay out of `defs`/`refs`, the map and embeddings. On a Unity project's
  question set, docs answers cost 29% less overall and 80–87% less when the
  section ranks in the top 3 (6 of 10 questions); cross-language questions
  (Russian question, English doc) still miss.
- **Docs next to code.** `outline` of a source file names the nearest module
  docs in one line (`docs: Mod/{AGENTS.md, MAP.md} · Editor/Documentation/…`).
- **Binding docs.** A document an `AGENTS.md` calls binding (`binding`,
  `обязателен`) is marked in `outline` and `search`: read it in full.
- **`LOTSMAN_INDEX`** relocates the index file — for benchmarks and CI caches
  that must not touch a project's own index.
- `doctor` reports whether SQLite has FTS5 (docs search).
- Index format 5: the first run rebuilds the index.
- **Query log covers the CLI and switches itself off.** A flag file
  `.lotsman/querylog.on` under the indexed root turns logging on for CLI read
  commands (`map`, `search`, `outline`, `slice`, `defs`, `refs`, `impact`) and
  MCP alike; each row records `via: cli|mcp`. The flag holds its last day
  (`until 2026-09-17`) or expires 14 days after it was created, so a
  measurement week cannot silently become permanent. `LOTSMAN_QUERYLOG=0`
  disables logging even with the flag (benchmarks use it); `lotsman report`
  shows the state and the CLI/MCP split.

## 1.6.0 — 2026-09-10

C# / Unity release: references that see how C# code actually uses types,
with line numbers, plus quieter maps and cheaper slices.

### C# / Unity

An independent review on a Unity project (1.3k C# files) found `refs`
reporting 0 of 221 files for a static class. Fixed and measured with the new
`benchmarks/bench_unity.py` (recall of `refs` against a comment- and
string-stripped `grep -rlw`, by declaration kind). The index format changed
(`INDEX_VERSION` 4): the first run after upgrading rebuilds it automatically.

- **C# `refs` see every PascalCase use site** — static receivers
  (`ExLog.Info`), enum values, constants, generic arguments
  (`GetComponent<T>`, `List<T>`), property/event/indexer types, `typeof`,
  `is`/`as`/casts, `nameof`, attribute arguments, method groups and
  `?.` calls; declaration names, namespaces and camelCase locals stay out.
  `[Test]` also counts as a use of `TestAttribute`. Recall by kind, before →
  after: static class 0.5% → 98.9%, enum 25% → 94%, class 73% → 99.5%,
  interface 88% → 100%, struct 76% → 100%, delegate 0% → 100%,
  method 78% → 99%, property 7% → 89% (camelCase properties are skipped by
  design — they look like locals).
- **`refs` prints `file:line`** — lines grouped per file (`path:12,40,88
  (+7)`, 6 per file), with the total of files and lines, so agents no longer
  grep afterwards to find the sites. JSON gains `lines`, `ambiguous` and, for
  `Type.Member`, `same_line`.
- **Ambiguity is flagged** — when several files define the name, `refs` says
  so and suggests `Type.Member`. `Type.Member` now narrows the definitions to
  files that define `Type`, lists files where both names share a line first
  and the rest as "weaker"; files defining another `Member` are no longer
  excluded from its references.
- **Case-sensitive stopwords for AST references** — a class `Window` or a
  property `Type` was invisible to `refs`; map ranking and `impact` still
  ignore such generic names.
- **Signatures skip attribute lines** — 19% of C# signatures (`[Test]`,
  `[MenuItem(...)]`) showed only the attribute; now the declaration, with
  same-line attributes kept (`[SerializeField] private int hp;`). Java
  annotations and TS decorators likewise. Symbol lines point at the
  declaration; `slice` still shows the attribute lines.
- **C# fields, events, delegates, records** — listed by `outline`/`defs`/
  `refs`; runs of fields/events collapse to one `[fields] a, b, c` line.
  Fields/events stay out of search, embeddings and the map (they displaced
  up to 8 of 10 search hits when included).
- **Map noise** — C# member names count toward a definition only from files
  that also name its owning type (`dict.TryGetValue` no longer ranks a
  repo's `TryGetValue`); `map --mention X` now personalizes PageRank toward
  files defining and using `X` (direct neighbors of `WindowsManager` in the
  map: 9/44 → 13/33 files); Unity-hidden folders (`Samples~/`, and dot-folders
  in Unity projects) are no longer indexed. Python maps are unchanged
  (checked byte-for-byte on the stdlib).
- **No silent regex fallback** — if a tree-sitter grammar fails to load,
  every CLI run warns on stderr and MCP tool output carries the warning; the
  index rebuilds when a grammar breaks or recovers instead of mixing rows.
- **`impact` separates name-only matches** — for C# members, files that
  never name the owning type are listed apart as "name-only matches" (never
  dropped); extension methods are exempt. Example: 138 impacted files for a
  UI helper became 2 direct + 136 name-only.
- **`slice` shows local context** — enclosing types plus 3 sibling
  signatures each side instead of the whole-file skeleton: 3.5k → 1.0k chars
  for one method in a 360-line file, 16k → 0.7k in a 2.3k-line one.
- Signature whitespace runs are squeezed (`Data     data` → `Data data`).
- Django, WordPress, Vite, Gin and ripgrep benchmark gates pass with
  unchanged token savings; tests also run green on Python 3.10 and 3.12.

### Also since 1.5.0

- **`lotsman update`** — explicit maintenance command for refreshing managed
  agent artifacts and the local index after upgrading Lotsman or changing
  project source. Without `--agent`, it auto-detects existing supported agent
  files; use `--agent` to add or force a specific integration.
- **Install UX docs** — README now explains the difference between running
  Lotsman from a checkout with `python3 -m lotsman` and installing the
  `lotsman` command with `python3 -m pip install -e ".[embeddings]"`.
- **Generated skill refresh** — the Codex-format skill now mentions the
  `python3 -m lotsman` fallback, and the Claude Code skill is updated for the
  v1.5 workflow: `slice`, qualified `refs Class.Method`, `impact --tests`,
  `report`, and `.lotsman/wiring.json`.

## 1.5.0 — 2026-07-08

Sharper release gates for real agent workflows: narrower reads, more honest
reference lookup, and better escape hatches for DI-heavy repos.

- **`lotsman slice`** — body-plus-skeleton retrieval for one symbol, available
  through both CLI and MCP, so agents can inspect the useful code without
  dragging a whole file into context.
- **Qualified `refs`** — `refs Class.Method` narrows same-named methods across
  classes, reducing the classic "every `render` looks the same" ambiguity while
  keeping the heuristic contract honest.
- **Wiring patterns** — integration docs now show how to seed DI-heavy or
  reflection-heavy repositories with `wiring.json` so config-only edges can be
  surfaced as candidates instead of vanishing from the map.
- **`impact --tests`** — impact output can surface likely test files alongside
  affected code, turning the heuristic blast radius into a quicker re-check
  shortlist after edits.
- **Opt-in query telemetry + `report`** — `LOTSMAN_QUERYLOG=1` records command
  usage, and `lotsman report` turns that local log into a human-readable
  workflow summary for tuning prompts, habits, and release gates.

## 1.4.0 — 2026-07-05

Onboarding and multi-agent support: from "clone, read, wire configs by hand"
to one command.

- **`lotsman init`** — one-command onboarding for any repository: writes the
  navigation policy into AGENTS.md (marker-delimited, idempotent, preserves
  existing content), creates a commented `.lotsmanignore` skeleton, adds
  `.lotsman/` to `.gitignore`, builds the index and warms the rank cache.
  `--agent claude` adds `.mcp.json` (merged, never clobbered) and a CLAUDE.md
  pointer importing AGENTS.md; `--agent cursor` adds `.cursor/mcp.json`;
  `--agent codex` prints the one-time global `codex mcp add` registration.
- **`docs/INTEGRATIONS.md`** — per-agent setup guide (Claude Code, Codex CLI,
  Cursor, generic CLI agents) and workflow lifehacks: cache warm-up on merge,
  `--mention` personalization, `doctor --fail-on-warn` as a pre-flight gate,
  `.lotsmanignore` before first impressions, AGENTS.md as the single source
  of truth.
- AGENTS.md is now the canonical policy location (the emerging cross-agent
  standard, read natively by Codex and Cursor); CLAUDE.md imports it via
  `@AGENTS.md` instead of duplicating.

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
