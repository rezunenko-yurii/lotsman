# Lotsman 1.7 plan — Unity / C# game projects

Status: draft, 2026-09-10. This file is also the working state of the release:
update the checkboxes and "Measured" lines as items land.

## Goal

An agent working on a Unity project gets **any project fact cheaply and fast**:
code, docs, JSON configs, assembly and platform setup, assets, compile errors.
Offline and headless — no running Unity Editor or IDE — so it works in CI,
background agents and cloud sessions, where Unity MCP and Rider are absent.

Reference corpus: SDK-Kitchen-V3 `Assets/` (private, read-only). Second
corpus: a public content-heavy Unity game (item G1), because SDK-Kitchen has
almost no serialized wiring (0 UnityEvent calls, ~30 scripts used by ~20 assets).

## Principles

1. **Measure first.** Every item starts with a baseline on the question set
   (B0) and ends with a before/after number. No number, no "done".
2. **Extend existing tools; add commands only where nothing fits.** Every MCP
   tool definition is paid for in every session (today: 7 tools, ~860 tokens).
   Budget: at most +2 MCP tools and +40% tool-definition tokens for 1.7.
3. **Output budgets stay.** New sections are capped per file/per item and
   say how much was hidden; a heuristic says it is a heuristic.
4. **Offline and name-based.** No Editor, no type resolution. Where Rider or
   Unity MCP would answer better, the output may say so.
5. **Not an asset browser.** Search/read/list of assets is covered by Unity
   CLI and community MCP servers; we only build what feeds `refs`, `change`,
   `impact` and cheap project facts.
6. No regressions: all existing benchmark gates (Django, WordPress, Vite, Gin,
   ripgrep, Unity recall) pass; `refs`/`outline` ≤ 0.3 s on SDK-Kitchen;
   no-op reindex ≤ 0.3 s; cold index ≤ 2× the 1.6 time.

## Evidence behind the scope (SDK-Kitchen, 2026-09-10)

| Source | Size | Covered in 1.6 |
|---|---|---|
| C# | 1297 files, 8.4 MB | yes |
| Markdown docs | 1487 files, 11.7 MB; root AGENTS.md makes agents read AGENTS.md / MAP.md / docs first; `ExDi/Docs/DECISIONS.md` is declared binding | no |
| JSON configs | 193 files; 46 name C# types (`"$type": "Device.Connection.InternetManagerSettings"`); key prefixes map to code: `p_` property (`p_Checker` → `Checker`), `f_` field, `t_` type (`t_ExWindowsConnector`); enum values as strings in `$merge`; some configs are JSON with a `.rc` extension | no |
| `.asmdef` | 25 compiled (outside ignored and `~` folders); references by name and by `GUID:` | no |
| `#if` | 441 C# files; per-platform `scriptingDefineSymbols`; asmdef `defineConstraints` | no |
| Prefabs / scenes / assets | 25 / 4 / 46 text-YAML; ~30 project scripts used by ~20 assets; missing-script components exist | no |
| Compile errors | `~/Library/Logs/Unity/Editor.log` (Unity 6000.5+: project `Logs/`) | no |

No usage telemetry exists yet (querylog is off everywhere) — B0 and T0 fill
that gap.

## Work items

### B0 — Question-set benchmark (first, blocks everything)

`benchmarks/bench_unity_questions.py`: 20–30 real questions on SDK-Kitchen,
each with a machine-checkable expected answer (file, `file:line` range, doc
section anchor, or file set). Categories: docs (≥ 8), change safety (≥ 6),
config/data (≥ 4), assembly/platform (≥ 3), errors (≥ 1). Two paths per
question: **baseline** (scripted grep + whole-file reads, as an agent without
lotsman would) and **lotsman** (the intended calls). Metrics: tokens, number
of calls, answer found (top-k / recall). Question sources: module `tasks.md`
findings, AGENTS.md rules, 1.6 review scenarios.

- [x] question set written (2026-09-10): 28 questions in the gitignored
  `benchmarks/private/q_{docs,change,data_asm,errors}.json` — docs 10,
  change 7, data 5, assembly/platform 4, errors 2 (fixture
  `fixtures/Editor.errors.log`); every expected file pinned by sha256[:12].
  Ground truth: file reads; change set by grep + manual confirmation of each
  hit (Rider `analyze_calls` resolved no C# methods in this setup).
- [x] reviewed with the user (2026-09-10; defaults: comment-only / unread
  JSON-string hits are optional files; data-03 LOCKED rule and data-05 missing
  GUIDs verified in code)
- [x] harness `benchmarks/bench_unity_questions.py` (generic, in repo) +
  private plans `benchmarks/private/make_plans.py` → `plans.json`; models
  Claude Code limits (30k chars per command output, 2000 lines per read,
  2000 chars per line)
- [x] baseline numbers recorded here (2026-09-10, lotsman 1.6.0 + querylog
  flag; raw: `benchmarks/private/b0_baseline_2026-09-10.json`)

**Measured B0 baseline** (tokens = chars/4 of everything the agent saw):

| Category | Qs | No lotsman: tokens / calls / found | lotsman 1.6: tokens / calls / found |
|---|---:|---|---|
| docs | 10 | 51,620 / 29 / 1.00 | 53,778 / 39 / 1.00 (search finds no docs: +4%) |
| change | 7 | 21,866 / 20 / 1.00 | 9,551 / 15 / 0.98 (−56%; noise 1–44 files, mostly from `impact`) |
| data | 5 | 54,199 / 21 / 1.00 | 47,626 / 18 / 1.00 (JSON still via grep; data-02 alone 45k) |
| assembly | 4 | 4,272 / 15 / 1.00 | 4,519 / 17 / 1.00 |
| errors | 2 | 330 / 3 / 0.50 | 330 / 3 / 0.50 (stale-log trap missed by both) |
| **all** | 28 | **132,287 / 88 / 0.96** | **115,804 / 92 / 0.96** |

How to read it:
- The no-lotsman plans were written by people who knew the answer, so they
  read the right files first: `found` ≈ 1.0 and tokens are a **lower bound**
  for a real agent. The comparison is cost of a competent path, not luck.
- Tokens concentrate in **docs** (whole-file reads of 500–1000-line guides)
  and **data** (opening every JSON hit) → D1 and J1 carry the savings.
- **change**: lotsman already saves tokens; the gap is *noise* — `impact`
  lists 30–44 unrelated files → C1 must not reuse `impact` wholesale.
- **assembly / errors** are cheap by grep; their value is correctness
  (err-02 staleness trap: 0/1), so judge A1/P1/U0 by `found`, not tokens.

Found while measuring (add to C1/J1):
- [x] enum members are not indexed as definitions: `refs ArrayMergeStrategy.Union`
  shows two `Union` *methods* of another class under "defined in". Fixed with
  D1: C# enum members are `field` symbols (outline collapses them; not ranked);
  the right definition shows and `ArrayMergeApplier.cs` is back in the refs.
- [ ] enum/member names inside C# string literals (test JSON) are invisible to
  AST refs (change-06 misses `NodeConfigCompositionPipelineTests.cs`) —
  candidate for J1's string scan or an explicit "strings" hint.

### T0 — Usage telemetry on SDK-Kitchen

Turn on `LOTSMAN_QUERYLOG=1` in SDK-Kitchen's `.mcp.json` (user's file — only
with the user's go-ahead) for a week of normal work; `lotsman report` feeds
the question set and priorities.

- [x] 2026-09-10: `LOTSMAN_QUERYLOG=1` added to SDK-Kitchen `.mcp.json` (MCP
  calls, from the next MCP server start); flag file
  `Assets/.lotsman/querylog.on` created.
- [x] CLI logging: flag file enables logging for CLI read commands too
  (`via: cli|mcp` in each row); the flag expires by itself (`until DATE`, else
  14 days). Commit f82cb5f, fast-forwarded into `main` 2026-09-10 (not pushed).
- [x] SDK-Kitchen switched to the flag only: `Assets/.lotsman/querylog.on` =
  `until 2026-09-17`; `LOTSMAN_QUERYLOG` removed from `.mcp.json`; log reset
  (2 rows from benchmark research deleted). The B0 harness runs lotsman with
  `LOTSMAN_QUERYLOG=0`.
- [ ] read `lotsman report` after a week (~2026-09-17)

### Tier 1 — core

**D1. Docs as first-class.**
- Markdown headings become symbols (`kind: section`, line ranges; explicit
  `{#anchor}` kept); `outline file.md` shows the heading tree.
- `search` returns doc sections next to code; code hits must not be
  displaced (existing search gates unchanged; docs weighted separately).
- Docs for code: `outline`/`slice` of a C# file name the nearest module docs
  (`AGENTS.md`, `overview.md`, `reference.md`, …) in one line.
- Binding docs guard: a doc named binding by an AGENTS.md (or matching a
  configurable list) is flagged "binding — read in full" in outline/search.
- Docs stay out of the code map ranking.
- Done when: docs questions in B0 find the expected section in top-3 and
  cost ≤ 25% of the baseline tokens; cold index ≤ +60%.

Status 2026-09-10 — implemented (uncommitted in the 1.7 branch):
`lotsman/docs.py` (ATX/setext headings, code/front matter/comments skipped,
Snowball Russian stemmer, `ё`→`е`, identifier subwords), sections stored as
`kind: section` (outline only; not defs/refs/map/code search/embeddings),
FTS5 table `docs_fts` (heading chain weight 4, own text 1; Russian stems
prefix-matched), `search` prints a `docs:` block after code hits with the
section's *own* range and a snippet read from disk, `outline` of code prints
one `docs:` line (nearest module, most useful docs first), binding docs
detected in AGENTS.md/CLAUDE.md and marked, `doctor` reports FTS5,
`LOTSMAN_INDEX` env relocates the index file (benchmarks must not rebuild a
project's live index). `INDEX_VERSION` 5.

Measured (B0 docs, authored agent queries; `lotsman17` = search → read top-3
doc hits → grep fallback only if the answer was not seen):
- expected section in top-3: **6/10** (01, 02, 06, 08, 09, 10), 7/10 in top-10;
- tokens: **36,742 vs 51,620 baseline (−29%, 71% of baseline)**; a hit costs
  1.1–1.6k tokens (−80…−87%), a miss ~1.5k more than baseline;
- **not met: ≤ 25% of baseline.** The 4 misses are lexical gaps, not bugs:
  3 are Russian queries against English decision docs (ExDi / GameSdkBeta
  DECISIONS.md, BUILD_ASSURANCE_DECISIONS.md), 1 lands on a sibling section of
  the same topic. Latin prefix matching and weight changes did not move them.
- cold index SDK-Kitchen 2.5 s → 3.8 s (+53%, met); DB 6.5 → 26 MB without
  embeddings; `refs`/`outline` 0.16–0.17 s (≤ 0.3, met); no-op reindex 0.11 s.
- no regressions: Django/WordPress/Vite/Gin/ripgrep gates pass with unchanged
  savings; Unity refs recall and map identical; MCP tool text +2%.

Whole B0 on 1.7 code after D1 (`b0_d1_2026-09-10.json`): **98,885 tokens vs
132,287 baseline (−25%) and 115,804 with lotsman 1.6 (−15%)**; docs 36,742,
change 9,519, data 47,626, assembly 4,668, errors 330.

Options for the misses (decision pending): multilingual embeddings for doc
sections as an optional extra; a search hint telling agents to mix English and
Russian terms; `outline` of the top hit's file as the fallback instead of grep.

**C1. `change Type.Member` (new command + MCP tool).**
One capped report: definition(s) and signature; overrides / implementations
(same member name in types that list the owner in their base list, name-based);
callers `file:line` (strong/weak as in `refs`); tests among callers; data
references (J1/U1 once they land); assembly visibility (A1); "Rider
`analyze_calls` gives type-resolved callers" hint.
- Done when: on B0 change questions the report contains ≥ 95% of the expected
  files (ground truth: Rider find-usages where available, else manual), is
  smaller than `refs` + `impact` combined, and costs ≤ 30% of the baseline.

### Tier 2 — project data and facts

**J1. JSON configs → `refs`.** Index JSON: `$type`/type-name strings resolve
to C# types; keys map to members through configurable conventions (exact
name, prefixes: ExDi uses `p_` property, `f_` field, `t_` type; set in
`.lotsman/wiring.json` or a new `json` section); enum member names found as
string values; configurable extra extensions (`.rc` holds JSON in SDK-Kitchen). `refs` gets a "data" block with `file:line`; `outline file.json`
shows top-level structure with lines.
- Done when: all 46 type-referencing JSON files found for their types;
  renaming check on 10 sampled members finds every JSON key.

**A1. Assemblies (`.asmdef`).** Map file → assembly (nearest asmdef up the
tree), assembly references (names and `GUID:` via `.asmdef.meta`), platforms,
`defineConstraints`. Surfaces: one header line in `outline`; `change`/`impact`
flag callers in assemblies that cannot see the owner; a facts command (U0).
- Done when: file→assembly and reference graph match Unity's `.csproj`
  files in the project root for all 25 compiled asmdefs.

**P1. `#if` and define symbols.** Parse `#if/#elif/#else` regions; outline
marks symbols under a condition (`[#if UNITY_IOS]`); per-platform defines from
ProjectSettings + asmdef `defineConstraints`/`versionDefines` + `csc.rsp`;
answer "is this compiled on iOS/Android/Editor?" in the facts command.
- Done when: conditions correct on 20 sampled symbols; outline growth ≤ 5%.

**U0. `unity` facts command (new command, folded into one MCP tool with C1
if the tool budget requires).** `lotsman unity [file|type]`: assembly and its
references, platforms, active defines per platform, and **compile errors from
Editor.log** (`error CS…` → `file:line`, with `staleLog` / `errorsPredateEdits`
flags; idea from Unity Open MCP).
- Done when: errors parsed on real logs from Unity 2022/6000; staleness flags
  tested.

**U1. Serialized assets → `refs`.** `.meta` GUID → script; `m_Script` in
prefabs/scenes/ScriptableObjects; UnityEvent persistent calls
(`m_TargetAssemblyTypeName` + `m_MethodName`); serialized field names;
broken references (GUID without `.meta`, idea from uasset-reference-mcp) as
their own result. Asset-level plus method-level; prefab variants/nested
prefabs followed by GUID.
- Done when: on the game corpus (G1) prefabs using a script and methods
  called from UnityEvents match a manual check on ≥ 10 samples.

**V1. Caller check after an edit (in `impact`).** For changed methods whose
parameter count changed (old signature from the index before refresh vs new),
count arguments at call sites in referencing files and list callers that
still pass the old count (`file:line`). Name-based; optional/`params`
arguments reported as "maybe".
- Done when: synthetic tests for add/remove/optional/params; on SDK-Kitchen a
  seeded change is caught.

### Tier 3 — cost and convenience

- **X1. Unused candidates** `lotsman unused [path]`: types/methods with no
  code, data or asset references, excluding Unity messages (`Awake`,
  `Update`, …), serialized callbacks and public package API; always labelled
  candidates with the reason. Done: false positives ≤ 20% on a manual sample.
- **X2. "Why does A depend on B"** `lotsman why A B`: shortest reference path
  between two files/types with the names on each edge (idea from
  uasset-reference-mcp `trace_path`).
- **X3. Index snapshot**: `lotsman index --export PATH` writes a compressed
  index (stdlib `lzma`/`zlib`) meant for the repo or CI cache; a missing index
  restores from it, then refreshes incrementally.
- **X4. Profiles and cursors**: `--profile compact|full` and a continuation
  cursor for `refs`, `search`, `impact`, `change`; MCP returns `next_cursor`
  (idea from Unity Open MCP) instead of truncating.
- **X5. `brief "task"`**: one budgeted call — mention-personalized map, code
  and doc search hits, relevant AGENTS.md sections. Only if B0/T0 show that
  the start of a task is a real cost.

### G1 — Public game corpus

Pick a content-heavy public Unity project (prefabs, scenes, UnityEvents,
ScriptableObjects; permissive license), pin a tag, clone read-only into a temp
dir inside the benchmark like the other harnesses. Candidates to evaluate:
Unity "Boss Room", "Chop Chop" (Open Projects). Needed for U1 and X1.

## Order

B0 → D1 → C1 → J1 → A1 + P1 + U0 → V1 → G1 → U1 → X1–X5.
Release 1.7 when tiers 1–2 meet their done criteria; tier 3 items ship in 1.7
if ready, otherwise 1.8. Each item: tests, benchmark numbers here, CHANGELOG
entry; `INDEX_VERSION` bump when the schema changes.

## Open questions

- Binding docs: how are they marked in other projects (convention vs config)?
- JSON conventions beyond ExDi (`p_` prefix, `$type`): which other config
  formats matter (Addressables groups, localization tables)?
- Game corpus license and size.
- Whether `change` and `unity` share one MCP tool to stay within the budget.
