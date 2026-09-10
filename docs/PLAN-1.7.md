# Lotsman 1.7 plan — Unity / C# game projects

Status: draft, 2026-09-10. This file is also the working state of the release:
update the checkboxes and "Measured" lines as items land.

## Handoff (read first in a new session)

State on 2026-09-10 (later session): A1, P1, U0 and V1 done and committed too;
**next: G1 → U1, then X1–X5**. B0 now 45,869 tokens, found 0.99. Before that: B0, T0, D1, C1, J1 done and committed on branch
`claude/wizardly-agnesi-511e02` (worktree
`/Users/exhale/Projects/ClaudePlayground/lotsman/.claude/worktrees/wizardly-agnesi-511e02`).
`main` has only the query-log commit (f82cb5f); the 1.7 branch is not merged,
nothing is pushed. B0 total: 132,287 tokens without lotsman → 48,870 on the
branch (−63%). **Next: A1 + P1 + U0** (judge by `found`, not tokens), then V1,
G1, U1, X1–X5. D1's ≤ 25% docs target is not met — options listed under D1.

Where things are:
- Question set, plans, fixtures, run results: `benchmarks/private/`
  (gitignored — exists only in this worktree; never commit it: private corpus).
  Regenerate plans: `python3 benchmarks/private/make_plans.py`. Add a
  `lotsman17` plan per new item there (pattern: lotsman call → `until_found` →
  grep fallback).
- Corpus: `/Users/exhale/Work/Projects/Cloud Repositories/SDK-Kitchen-V3/Assets`
  — read-only. Its own index `Assets/.lotsman/index.db` is used by the user's
  sessions through `main` (INDEX_VERSION 4): **never run branch code on it**
  (the branch is at 7 → rebuild ping-pong). Use a scratch index:
  ```bash
  export LOTSMAN_INDEX=<scratchpad>/b0index/index.db LOTSMAN_QUERYLOG=0
  python3 -m lotsman --repo "<corpus>/Assets" index      # ~4 s + embeddings
  python3 benchmarks/bench_unity_questions.py --project "<corpus>" \
    --questions 'benchmarks/private/q_*.json' --plans benchmarks/private/plans.json \
    --paths baseline,lotsman17 --index <scratchpad>/b0index/index.db
  ```
  `LOTSMAN_QUERYLOG=0` keeps the user's telemetry clean (flag
  `Assets/.lotsman/querylog.on` = `until 2026-09-17`; read `lotsman report`
  after that date — T0).
- Gates before each commit: `python3 -m unittest discover -s tests`; after
  ranking/extraction changes also the five corpus benches
  (`benchmarks/bench_{django,wordpress,vite,gin,ripgrep}.py`, they clone
  pinned tags) and `benchmarks/bench_unity.py --root <corpus>/Assets --db <scratch>`.
- Rules the user set: commit per finished item on the 1.7 branch; no push or
  merge to `main` without asking; SDK-Kitchen files only with explicit
  permission (done so far: `.mcp.json` query-log change, one entry pair in
  `ExWindows/Editor/Documentation/tasks.md`); third-party tools — read their
  docs for ideas, never install.
- Environment gotchas: a local hook rejects any Bash command containing the
  word `tree` (even inside Python code) — put such code in a scratch `.py` file
  or use Edit; zsh does not word-split `$var` (use arrays or Python drivers);
  Rider MCP is connected but `analyze_calls` resolved no C# methods.
- Budgets that still apply: MCP tools now 9 / ~1,100 tokens (json chars/4;
  `unity` added in A1 — no more new tools; U0 extends `unity`);
  `refs`/`outline`/`unity` ≤ 0.3 s (now ~0.17 s); cold index ≤ 2× 1.6 (now
  3.9 s vs 2.5 s).
- Shell gotcha: `cd` outside the worktree resets the session cwd to the main
  checkout — prefix commands with `cd <worktree> &&` or use absolute paths.
- The user's Editor.log (`~/Library/Logs/Unity/Editor.log`) contains a Hub
  access token in its command-line block: never print log headers.

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

Status 2026-09-10 — implemented (`lotsman/changeview.py`, CLI `change
TARGET [-k N] [--json]`, MCP tool `change`): C# inheritance edges from
`base_list` (generic, qualified, record bases) stored in table `bases`
(INDEX_VERSION 6); type family = target type + transitive supertypes +
transitive subtypes; report = definition (narrowed to the owner type),
"declared by supertypes", overrides/implementations in subtypes, callers
`file:line` only in files naming the family (type on the line first), tests
marked, other same-name uses counted as `name-only`; extension methods are not
gated; one `not covered` line (JSON/asset configs, reflection by string).
Assembly visibility waits for A1, Rider hint dropped (the output says
name-based; keep tokens). MCP tools: 8, tool text +16% vs 1.6 (budget 40%).

Measured (B0 change, `lotsman17` = `change` → grep JSON/strings only if
something is still missing): **3,936 tokens vs 21,866 baseline (18%, met) and
9,515 for 1.6 `refs`+`impact`**; found 0.98 (met; the miss is a member name
inside a C# string literal); noise 0–2 files vs 29–44 with `impact`; the
report alone is 230–380 tokens where no JSON is involved (met: smaller than
`refs`+`impact`). `change` runs 0.16–0.21 s on SDK-Kitchen.
Whole B0 after C1 (`b0_c1_2026-09-10.json`): **93,302 tokens vs 132,287
baseline (−29%)**; all corpus gates pass, Unity refs recall unchanged.
Generated agent policy/skills now route signature changes to `change`.

### Tier 2 — project data and facts

**J1. JSON configs → `refs`.** Index JSON: `$type`/type-name strings resolve
to C# types; keys map to members through configurable conventions (exact
name, prefixes: ExDi uses `p_` property, `f_` field, `t_` type; set in
`.lotsman/wiring.json` or a new `json` section); enum member names found as
string values; configurable extra extensions (`.rc` holds JSON in SDK-Kitchen). `refs` gets a "data" block with `file:line`; `outline file.json`
shows top-level structure with lines.
- Done when: all 46 type-referencing JSON files found for their types;
  renaming check on 10 sampled members finds every JSON key.

Status 2026-09-10 — implemented (`lotsman/jsonrefs.py`; INDEX_VERSION 7):
`.json` and content-sniffed `.rc`; tolerant tokenizer (comments, trailing
commas); keys and string values count as references when they name code in
PascalCase after stripping markers (`-------LOCKED-------`), `$`/`*` and
member prefixes (`p_`/`f_`/`t_`, hardcoded defaults — no config needed for
ExDi; make configurable when another convention shows up); dotted and
`Type, Assembly` values → last name; files > 256 KB, generated one-liners and
non-JSON `.rc` skipped. Keys up to depth 2 are `key` symbols (outline only).
JSON/Markdown never feed the map graph; JSON does feed `impact`. `refs`
prints a `data —` block, `change` a `data —` block marking configs that do not
name the type (`"$merge": "Union"`). Repo-root docs no longer print a
`docs:` line in outline. Per-word term cache: cold index 3.6 s (1.6: 2.5 s).

Measured: done criteria met — 159/159 (file, type) string mentions in 47
JSON files found by `refs`; renaming check 10/10 members fully covered.
B0 (`b0_j1_2026-09-10.json`): **data 54,199 → 3,966 tokens (−93%; data-02
45k → 93)**, change 3,164 (JSON now in `change`, no grep fallback);
**all 28 questions 132,287 → 48,870 (−63%)**.

**A1. Assemblies (`.asmdef`).** ✅ Map file → assembly (nearest asmdef up the
tree), assembly references (names and `GUID:` via `.asmdef.meta`), platforms,
`defineConstraints`. Surfaces: one header line in `outline`; `change`/`impact`
flag callers in assemblies that cannot see the owner; a facts command (U0).
- Done when: file→assembly and reference graph match Unity's `.csproj`
  files in the project root for all 25 compiled asmdefs.

Status 2026-09-10 — implemented (`lotsman/unity.py`, INDEX_VERSION 8):
`.asmdef`/`.asmref` scanned as lang `asmdef`; table `assemblies` (kind, name,
JSON with key lines, per-reference lines, GUID from `.meta` — a `.meta`-only
GUID change is not detected until the asmdef changes); meta key
`unity_assets` set when the repo is a Unity project or its `Assets/`.
Model: nearest asmdef/asmref folder, else predefined (`Plugins/`,
`Standard Assets/` → firstpass; `Editor` folder → `-Editor`); references by
name/GUID (unresolved GUIDs = outside the index, e.g. `.lotsmanignore`d
vendors); predefined assemblies see every autoReferenced asmdef — Editor-only
ones too, but only in the Editor compile (verdict says player builds fail).
Surfaces: `lotsman unity TARGET [USES]` + MCP tool `unity` (file / type /
assembly; USES → visible or not + the asmdef line to edit); `outline` of `.cs`
prints `assembly: …`; `change` marks callers `[X cannot see Y]`. `impact` not
touched. Agent skill tables route Unity questions to `unity`.

Measured: **done criterion met** — `benchmarks/check_unity_csproj.py` on
SDK-Kitchen: 24 asmdefs in the index (plan said 25), file → assembly
1297/1297 match the generated `.csproj`, references 27/27 assemblies (24
asmdefs + 3 non-empty predefined; the empty Editor-firstpass csproj and the
stale `ExLog.Tests.Editor.csproj` are skipped). B0 (`b0_a1_2026-09-10.json`):
assembly 4,668 tokens / 17 calls → 4,711 / 10 (asm-01, asm-02: one `unity`
call each, found 1.0; asm-03/04 wait for P1); all 28: 48,913 tokens.
`outline`/`refs`/`unity` 0.17 s; cold index 3.9 s; `bench_unity.py` gates
pass; the five corpus benches not rerun (no `.asmdef` there — the change
cannot reach them).

**P1. `#if` and define symbols.** ✅ Parse `#if/#elif/#else` regions; outline
marks symbols under a condition (`[#if UNITY_IOS]`); per-platform defines from
ProjectSettings + asmdef `defineConstraints`/`versionDefines` + `csc.rsp`;
answer "is this compiled on iOS/Android/Editor?" in the facts command.
- Done when: conditions correct on 20 sampled symbols; outline growth ≤ 5%.

Status 2026-09-10 — implemented (`lotsman/csdefines.py`, INDEX_VERSION 9):
line scan of `#if/#elif/#else/#endif/#define/#undef` (BOM-safe; unterminated
branches run to EOF), table `directives` with the effective condition per
branch; three-valued evaluator; `ProjectDefines` read from disk on each
`unity` call (Player Settings groups by name or old numeric id, `csc.rsp` /
`mcs.rsp` at Assets root and next to an asmdef, ProjectVersion.txt version
symbols, active Editor target + Editor defines from `Assembly-CSharp.csproj`,
package versions from `Packages/packages-lock.json`/`manifest.json` for
`versionDefines`). Targets shown: Editor + groups present in Player Settings
(fallback iOS/Android/Standalone). Symbols matching Unity/.NET prefixes
(`UNITY_`, `ENABLE_`, `PLATFORM_`, `NET_`, …) are unknown, not undefined;
other symbols no source defines are "defined nowhere" (false). `outline`
marks symbols whose condition differs from the enclosing symbol's; `unity`
prints `compiled for`, `declared under #if` for symbol targets, conditions
grouped with `path:lines` (≤ 12 groups × 20 lines), `defined nowhere`, and
`project defines` with ProjectSettings line numbers (paths above an indexed
`Assets/` print as `../`; the B0 harness maps them).

Measured: **done criteria met** — conditions vs an independent oracle
(tree-sitter `preproc_*` ancestry, compared by truth table): 360/360 symbols
under `#if` in files tree-sitter parses cleanly (20 random samples printed and
read; 141 symbols in files with parse errors not oracle-checked; the oracle
found one real bug — BOM before a first-line `#if` — fixed); outline size
+0.50% over all 1297 C# files (21 files, 186 rows marked). B0
(`b0_p1_2026-09-10.json`): **assembly 4,272 tokens / 15 calls (baseline) →
1,490 / 5**, found 1.0 (asm-03 3,538 → 152 tokens; asm-04 787 vs 321 — the
report also shows the dead `!UNITY_2018_1_OR_NEWER` branch); **all 28:
132,287 → 45,692 (−65%)**. Cold index 3.65 s; `outline`/`refs`/`unity`
0.16–0.17 s; `bench_unity.py` gates pass; five corpus benches not rerun (no C#
there; C# fixture tests green).

**U0. `unity` facts command ✅ (new command, folded into one MCP tool with C1
if the tool budget requires).** `lotsman unity [file|type]`: assembly and its
references, platforms, active defines per platform, and **compile errors from
Editor.log** (`error CS…` → `file:line`, with `staleLog` / `errorsPredateEdits`
flags; idea from Unity Open MCP).
- Done when: errors parsed on real logs from Unity 2022/6000; staleness flags
  tested.

Status 2026-09-10 — implemented (`lotsman/unitylog.py`; `unity errors
[--log PATH]`, MCP `unity` with target `errors` + `log`): segments at
compile-start markers, last segment with compiler output = last compilation;
`path(line,col): error|warning CODE: msg` (backslashes normalised), fileless
`error CSxxxx` lines kept; dedupe; warnings counted by code; earlier distinct
errors counted when the last compilation is clean. Log choice: `--log`, else
`<project>/Logs/Editor.log` and the OS location, preferring the one whose
`-projectpath` matches (another project's log → refusal, no errors listed).
Staleness from the log mtime vs indexed `.cs`/`.asmdef`/`.asmref` mtimes.
Only the version line, the project path and compiler lines are read.

Measured: B0 (`b0_u0_2026-09-10.json`): **errors found 0.50 → 1.00**
(err-02 staleness trap caught: the WindowsManager.cs error line is marked
`[file edited after the log]`; question got `accept_output` for that line),
507 tokens / 2 calls vs 330 / 3; **all 28: 132,287 → 45,869 tokens (−65%),
found 0.96 → 0.99**. Done criterion: parsed on a real 6000.3 log (the user's
live Editor.log — clean — and the fixture: that log with injected errors);
the 2022 case is a synthetic log in `tests/test_unitylog.py` (no real 2022
log with errors available — **not verified on a real 2022 log**); staleness,
other-project, clean-after-failure and dedupe tested. `unity errors` 0.18 s.
MCP tool text 9 tools / ~1,156 tokens.

Design notes for P1 + U0 (worked out 2026-09-10; implemented as above):
- `#if` regions: line-based scan of `#if/#elif/#else/#endif` (C# directives
  are whole lines; tree-sitter shows them as `preproc_if/elif/else` but breaks
  on unbalanced branches). Store per C# file the regions with the *effective*
  condition of each branch (`#else` → `!(A)`, `#elif B` → `!(A) && (B)`,
  nesting ANDed) in a new table; outline appends `[#if X]` to symbols inside;
  `unity FILE` lists regions grouped by condition with `#if` lines (asm-03
  expects lines 148…244 of IosWebView.cs) and a verdict per target.
- Evaluation: tiny parser for `! && || == != ( ) true false`, three-valued
  (unknown symbols → unknown, `UNITY_IOS && !UNITY_EDITOR` in the Editor is
  false regardless). Targets: Editor + player groups from ProjectSettings
  `scriptingDefineSymbols` (names like `Android:`/`iPhone:`/`Standalone:`;
  Unity ≤ 2019 used BuildTargetGroup numbers 1 Standalone, 4 iPhone,
  7 Android, 13 WebGL), built-ins (`UNITY_EDITOR`, `UNITY_IOS`/`UNITY_IPHONE`,
  `UNITY_ANDROID`, `UNITY_STANDALONE*`, `UNITY_WEBGL`, version
  `UNITY_6000_3_OR_NEWER` from ProjectVersion.txt), `csc.rsp` (`-define:`,
  Assets root = all assemblies, next to an asmdef = that one), asmdef
  `versionDefines` against Packages/manifest.json, `defineConstraints` gate
  the whole assembly. Active Editor target: the generated
  `Assembly-CSharp.csproj` `DefineConstants` (SDK-Kitchen: UNITY_IOS).
  asm-04 needs "UNITYWEBVIEW_ANDROID_ENABLE_CAMERA defined nowhere" with the
  evidence `ProjectSettings/ProjectSettings.asset:820` in the output.
- Files outside the indexed root print as `../ProjectSettings/…`; the harness
  must map a leading `../` to project-relative (today `rel()` strips it and
  prefixes `Assets/`).
- Errors: `path(line,col): error|warning CODE: msg` (same in 2019–6000);
  keep only the last compilation (segment at `[ScriptCompilation] Requested
  script compilation`, `Starting: …ScriptCompilationBuildProgram`, or 2019's
  `- Starting script compilation`); dedupe; warnings counted apart. Log
  candidates: `--log PATH`, `<project>/Logs/Editor.log`, then the OS default
  (macOS `~/Library/Logs/Unity/Editor.log`, Linux
  `~/.config/unity3d/Editor.log`, Windows `%LOCALAPPDATA%/Unity/Editor/Editor.log`);
  pick the one whose `-projectpath` matches, else say it belongs to another
  project. Staleness: source files (.cs/.asmdef) newer than the log's mtime
  → "errors may predate these edits; recompile"; errors in files edited after
  the log are marked. No per-line timestamps exist in 6000.3 logs. The real
  logs here have no `error CS`: the fixture is a real 6000.3 log with injected
  errors; a 2022 sample must be synthetic (say so).
- err-02 scoring: `value` without evidence needs 2 `stat`s today — add an
  `accept_output` regex to the question (e.g. `predate`) so a lotsman
  staleness line counts.

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

Status: **done** (2026-09-10). `lotsman/callcheck.py`; `symbols.arity` +
`sig_changes` table, index format 10. The baseline is the shape the index
held before the refresh that saw the edit; it stays across later edits,
reverting forgets it, it expires after 24 h (`SIG_CHANGE_TTL`). `impact`
puts "Signature changes" right after the note line, outside the budget cut.
Same-name calls of other types are skipped (and counted): receiver declared
with a type outside the owner's family (fields, locals, parameters,
properties, `var x = new T(`), static call on another indexed type, bare
call inside a type with its own same-name method, or a count the old shapes
never took. Extension methods: `Ext.M(x)` / bare call inside `Ext` pass
`this`, `x.M()` does not.

Measured (a copy of SDK-Kitchen `Assets` in the scratchpad, never the live
project; B0 has no V1 question — the harness cannot seed edits):
- Seeded `ExLog.AddSink(ILogSink)` → `(ILogSink, int priority)`: before,
  `impact` (6,157 chars) listed the 4 caller files among 208, `AddSink`
  named in 2, no lines, no break signal. After: **5/5 real calls (grep) in
  4 files as `breaks` with lines, 0 false**; the 7 same-name
  `PurchasingFeedbackPresenter.AddSink` calls skipped (an earlier cut
  without receiver types reported 2 of them).
- Seeded `InjectFields(this NodeBehaviour)` + `bool strict = false`:
  **15/15 calls as `maybe`** (13 `x.InjectFields()`, 2 static
  `Injector.InjectFields(b)`).
- Precision on compiling code (150 sampled files, every method given +1
  required parameter, 3,332 call sites): 1,950 flagged, 1,238 skipped as
  another type's; a manual check of 20 random flagged calls: 17 real
  callers (3 of them through a base/interface receiver; `var`/DI receivers
  stay unresolved), ~12/20 before receiver types. Object members
  (`ToString`…) among flagged: 72 → 9.
- Cost: cold index 3.48 → 3.75 s (+6%; a per-name regex first cost +26%,
  replaced by `str.find`, and each file is cleaned once); `impact` 0.20 s
  with or without seeded changes. Shapes for 10,060 of 12,053 C# "method"
  rows — the rest are properties; 1 real method (a long `extern`) missed.
- Found on the way: stripping strings before comments let a `"` inside a
  block comment open a fake string (SimpleJSON.cs header) — one-pass
  tokenizer now.

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
