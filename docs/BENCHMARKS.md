# Benchmarks

Numbers are produced by reproducible harnesses in [`benchmarks/`](../benchmarks).
Each harness pins or names the external corpus, builds the index from scratch,
measures update/read latency, and exits non-zero when quality gates fail.

```bash
python benchmarks/bench_django.py                     # clones Django itself
python benchmarks/bench_django.py --django-dir ~/src/django   # reuse a checkout

python benchmarks/bench_wordpress.py                  # downloads WordPress itself
python benchmarks/bench_wordpress.py --wordpress-dir ~/src/wordpress

python benchmarks/bench_vite.py                       # clones Vite itself
python benchmarks/bench_vite.py --vite-dir ~/src/vite

python benchmarks/bench_gin.py                        # clones Gin itself
python benchmarks/bench_gin.py --gin-dir ~/src/gin

python benchmarks/bench_ripgrep.py                    # clones ripgrep itself
python benchmarks/bench_ripgrep.py --ripgrep-dir ~/src/ripgrep
```

## Evidence model

There are three different evidence levels, and they should not be blurred
together:

| Evidence | What it proves | What it does not prove |
|---|---|---|
| Reproducible corpus benchmarks | Lotsman can reduce token cost and keep retrieval quality on pinned real repos | That every task or language gets the same savings |
| Quality gates | Map/search/defs still surface known useful files/symbols after ranking changes | Complete semantic correctness or type-resolved impact |
| Live agent sessions | An actual agent can use the tool instead of grep/read cascades | Broad statistical productivity impact without repeated A/B runs |

Current honest claim: Lotsman is useful when the task is code navigation in a
symbol-rich repo and the agent follows the narrow-retrieval workflow. Stronger
claims need repeated agent A/B runs: same task, same model, with/without
Lotsman, measuring tool calls, files read, tokens, elapsed time, correctness,
test choice, and whether the agent had to backtrack.

## v1.6 reference run

Rerun on 2026-09-10 with lotsman 1.6.0, Python 3.14 and the `[embeddings]`
extra installed; the five harnesses ran concurrently, so timings are upper
bounds. All quality gates passed, and every scenario's token savings are
unchanged from v1.5. The C# / Unity recall run is in its own section below.

| Corpus | Cold index | Warm map | Search | Scenario savings |
|---|---:|---:|---:|---:|
| Django 5.2 | 3.73 s | 0.07 s | 0.50 s | 24x |
| WordPress 7.0 | 2.52 s | 0.02 s | 0.13 s | 51x |
| Vite v5.4.11 | 0.53 s | 0.00 s | 0.03 s | 14x |
| Gin v1.10.0 | 0.18 s | 0.00 s | 0.01 s | 7x |
| ripgrep 14.1.1 | 0.37 s | 0.00 s | 0.04 s | 31x |

Important interpretation: the token-savings scenarios already modeled narrow
reads before v1.5. The v1.5 profit is therefore not a sudden new headline
ratio; it is that narrow reads, qualified references, affected-test shortlists,
DI/reflection mitigation, and query telemetry are now explicit product
surfaces instead of agent discipline or benchmark assumptions.

## Benchmark matrix

The goal is not one heroic number. Different ecosystems stress different parts
of the navigator, so benchmarks should cover both performance and retrieval
quality across several repo shapes.

| Corpus | Language / shape | What it stresses | Status |
|---|---|---|---|
| Django 5.2 | Python web framework | precise defs/refs, large symbol graph, hybrid search | implemented, quality-gated |
| WordPress 7.0 | PHP CMS package | PHP definitions, lexical PHP refs, vendor-ignore hygiene, mixed core/admin tree | implemented, quality-gated |
| Vite v5.4.11 | TypeScript monorepo | TS interfaces/functions, playground fixture noise, plugin/server config paths | implemented, quality-gated |
| Gin v1.10.0 | Go web framework | Go defs/refs, small-file framework layout, package-level APIs | implemented, quality-gated |
| ripgrep 14.1.1 | Rust workspace/CLI | Rust traits/structs/functions, multi-crate workspace, long flag/search files | implemented, quality-gated |
| Java service/library | Java backend | classes/interfaces, package layout, annotation-heavy code | planned |
| C# / Unity project | C# game/tooling repo | static classes, enums, generics, attributes; serialized references (text YAML assets) | `bench_unity.py`, quality-gated on a private corpus and on Chop Chop |
| Chop Chop (Unity Open Project #1, pinned commit) | Unity 2020.3 game, content-heavy | prefabs/scenes/ScriptableObjects using scripts, UnityEvent and animation-event method calls, broken references | `bench_unity_game.py`, gated since U1; baseline for X1 |

WordPress release archives include bundled libraries and large JS/admin assets.
The WordPress harness writes a fixture-local `.lotsmanignore` so the measured
perimeter is **WordPress PHP core**, not third-party package ranking. This is
intentional: Lotsman's docs recommend ignoring vendored/generated trees in real
projects too.

## Django Reference Run

Machine: MacBook (Apple Silicon), Python 3.14, lotsman 1.6.0, `[embeddings]`
extra installed.

| Operation | Time |
|---|---|
| Cold index (2272 files, ~41k symbols) | 3.73 s |
| Embedding pass (all symbols) | 1.27 s |
| No-op reindex | 0.14 s |
| Reindex after a 1-file edit | 0.13 s |
| `map` — cold (computes PageRank) | 1.01 s |
| `map` — warm (rank cache) | **0.07 s** |
| `search` (hybrid) | 0.50 s |

## Quality gates

Each run asserts result *quality*, not just speed, and exits non-zero on
failure.

Django gates make the baseline from [DESIGN.md](DESIGN.md) executable:

- the default map must contain `cached_property`, `ValidationError`,
  `ForeignKey` and must not be topped by generic method names
  (`value` / `list` / `request` / `data`);
- four navigation questions must surface the expected file in the top-5,
  e.g. `"validate unique fields model"` → `django/db/models/base.py`.

Gates are calibrated for hybrid search (the default when the `[embeddings]`
extra is installed); the harness embeds after indexing exactly like the CLI.

## Django Token-Cost Scenario

The scenario models an agent answering *"how does Django validate model field
uniqueness?"*:

- **lotsman path** — `search` → `defs` → `outline` → read only the ~140-line
  slice around `validate_unique` in `django/db/models/base.py`.
- **naive path** — read the three relevant files whole
  (`db/models/base.py`, `forms/models.py`, `db/models/query.py`).

| Path | Tokens (chars/4 estimate) |
|---|---|
| lotsman (tool output + slice) | ~2,800 |
| whole files | ~67,000 |
| **Savings** | **24×** |

The estimate is deliberately conservative: it ignores the second-order cost of
carrying 67k tokens in the context window for the rest of the session, which is
where most of the real-world money goes.

## WordPress Reference Run

Source: WordPress **7.0** from the official release zip
(`https://wordpress.org/wordpress-7.0.zip`). The benchmark fixture excludes
bundled dependencies/admin JS with `.lotsmanignore` and measures the PHP-core
navigation surface.

Machine: MacBook (Apple Silicon), Python 3.14, lotsman 1.6.0, `[embeddings]`
extra installed.

| Operation | Time |
|---|---|
| Cold index (839 files, ~9k symbols) | 2.52 s |
| Embedding pass (all symbols) | 0.60 s |
| No-op reindex | 0.05 s |
| Reindex after a 1-file edit | 0.11 s |
| `map` — cold (computes PageRank) | 0.35 s |
| `map` — warm (rank cache) | **0.02 s** |
| `search` (hybrid) | 0.13 s |

Quality gates cover different retrieval paths:

- the default map must include central WordPress core files such as
  `wp-includes/functions.php`, `plugin.php`, `class-wpdb.php`, and
  `class-wp-query.php`;
- `defs sanitize_text_field` must resolve to `wp-includes/formatting.php`;
- three behavior searches must surface REST dispatch, script/style enqueueing,
  and block parsing files in the top-5.

## WordPress Token-Cost Scenario

The scenario models an agent locating WordPress text-field sanitization:

- **lotsman path** — `search` → `defs sanitize_text_field` → `outline
  wp-includes/formatting.php` → read only the function slice.
- **naive path** — read three likely files whole: `formatting.php`,
  `option.php`, and `wp-admin/includes/schema.php`.

| Path | Tokens (chars/4 estimate) |
|---|---|
| lotsman (tool output + slice) | ~2,500 |
| whole files | ~126,000 |
| **Savings** | **51×** |

## Vite / TypeScript Reference Run

Source: Vite **v5.4.11**, shallow-cloned from
`https://github.com/vitejs/vite.git`.

Machine: MacBook (Apple Silicon), Python 3.14, lotsman 1.6.0, `[embeddings]`
extra installed.

| Operation | Time |
|---|---|
| Cold index (1131 files, ~2.2k symbols) | 0.53 s |
| Embedding pass (all symbols) | 0.46 s |
| No-op reindex | 0.05 s |
| Reindex after a 1-file edit | 0.06 s |
| `map` — cold (computes PageRank) | 0.10 s |
| `map` — warm (rank cache) | **0.00 s** |
| `search` (hybrid) | 0.03 s |

Quality gates cover a noisy TypeScript monorepo without special ignore rules:

- the default map must include core Vite paths such as config, server,
  plugin-container and plugin-index files;
- `defs createServer` must include `packages/vite/src/node/server/index.ts`,
  even though playground fixtures also define `createServer`;
- `defs resolveConfig` must resolve to `packages/vite/src/node/config.ts`;
- behavior searches must surface plugin-container transforms and config/plugin
  resolution in the top-5.

## Vite / TypeScript Token-Cost Scenario

The scenario models an agent locating Vite dev-server creation:

- **lotsman path** — `search` → `defs createServer` → `outline
  packages/vite/src/node/server/index.ts` → read only the function slice.
- **naive path** — read likely whole files: `server/index.ts`, `config.ts`,
  and `server/pluginContainer.ts`.

| Path | Tokens (chars/4 estimate) |
|---|---|
| lotsman (tool output + slice) | ~1,800 |
| whole files | ~26,200 |
| **Savings** | **14×** |

## Gin / Go Reference Run

Source: Gin **v1.10.0**, shallow-cloned from
`https://github.com/gin-gonic/gin.git`.

Machine: MacBook (Apple Silicon), Python 3.14, lotsman 1.6.0, `[embeddings]`
extra installed.

| Operation | Time |
|---|---|
| Cold index (92 files, ~1.2k symbols) | 0.18 s |
| Embedding pass (all symbols) | 0.69 s |
| No-op reindex | 0.02 s |
| Reindex after a 1-file edit | 0.03 s |
| `map` — cold (computes PageRank) | 0.01 s |
| `map` — warm (rank cache) | **0.00 s** |
| `search` (hybrid) | 0.01 s |

Quality gates cover:

- the default map must include central Gin files: `gin.go`, `context.go`,
  `routergroup.go`, and `binding/binding.go`;
- `defs Recovery` must resolve to `recovery.go`;
- `defs JSON` must include `context.go`, because `JSON` is also a renderer
  type and an error method;
- behavior searches must surface route groups, JSON binding, and the HTTP
  request engine in the top-5.

## Gin / Go Token-Cost Scenario

The scenario models an agent locating Gin's `Context.JSON` response path:

- **lotsman path** — `search` → `defs JSON` → `outline context.go` → read only
  the method slice.
- **naive path** — read likely whole files: `context.go`, `render/json.go`,
  and `gin.go`.

| Path | Tokens (chars/4 estimate) |
|---|---|
| lotsman (tool output + slice) | ~2,400 |
| whole files | ~16,900 |
| **Savings** | **7×** |

## ripgrep / Rust Reference Run

Source: ripgrep **14.1.1**, shallow-cloned from
`https://github.com/BurntSushi/ripgrep.git`.

Machine: MacBook (Apple Silicon), Python 3.14, lotsman 1.6.0, `[embeddings]`
extra installed.

| Operation | Time |
|---|---|
| Cold index (101 files, ~3.2k symbols) | 0.37 s |
| Embedding pass (all symbols) | 0.49 s |
| No-op reindex | 0.02 s |
| Reindex after a 1-file edit | 0.02 s |
| `map` — cold (computes PageRank) | 0.03 s |
| `map` — warm (rank cache) | **0.00 s** |
| `search` (hybrid) | 0.04 s |

Quality gates cover a Rust multi-crate workspace:

- the default map must include central flag, matcher, ignore-walk and searcher
  files;
- `defs SearchWorker` must resolve to `crates/core/search.rs`;
- behavior searches must surface ignore walking and command-line argument
  parsing in the top-5.

## ripgrep / Rust Token-Cost Scenario

The scenario models an agent locating ripgrep's search worker/printer flow:

- **lotsman path** — `search` → `defs SearchWorker` → `outline
  crates/core/search.rs` → read only the struct/search slice.
- **naive path** — read likely whole files: `crates/core/search.rs`,
  `crates/core/flags/hiargs.rs`, and `crates/printer/src/standard.rs`.

| Path | Tokens (chars/4 estimate) |
|---|---|
| lotsman (tool output + slice) | ~1,600 |
| whole files | ~51,200 |
| **Savings** | **31×** |

## C# / Unity Reference Recall

`benchmarks/bench_unity.py --root PATH/Assets` measures what `refs` finds on
any Unity project. For every C# declaration with a corpus-unique name it
compares the files `refs` reports with `grep -rlw` over the same files (the
indexed set, `.lotsmanignore` applied), and with a fairer "code" baseline:
grep after blanking comments, strings, `#` directives and namespace names via
tree-sitter. Defining files are excluded on both sides, like `refs` does. The
index goes to a scratch directory; the corpus is only read.

Run 2026-09-10 on a private Unity project (1297 C# files after skipping `~`
folders; 1388 before), Python 3.14. Recall = files found / "code" files:

| Kind | Symbols | grep | code | Before | After |
|---|---:|---:|---:|---:|---:|
| static class | 272 | 1440 | 1245 | 0.5% | 98.9% |
| enum | 114 | 591 | 580 | 25.4% | 93.6% |
| class | 722 | 2883 | 2621 | 73.0% | 99.5% |
| interface | 86 | 447 | 438 | 88.2% | 100% |
| struct | 46 | 142 | 128 | 75.8% | 100% |
| delegate | 8 | 13 | 13 | 0% | 100% |
| property | 421 | 2621 | 1945 | 7.3% | 89.4% |
| method | 858 | 3035 | 2711 | 78.0% | 99.0% |

Precision against the "code" baseline is 100% for every kind. Symbols the
independent review flagged: `ExLog` 0 → 207 of 207 files, `UiClasses`
0 → 55/55, `WindowDisplayMode` 9 → 21/21, `WindowsManager` 13 → 14/14.

What the residue is (checked by hand with `--explain`):

- grep-only hits (grep minus "code"): doc comments and `<see cref>`,
  commented-out code, string keys like `"ExLog.NodePath"` — not references;
- enum / static class misses: same-named declarations of other entities
  (an `ExifTag` enum member named like another enum, a `const string` named
  like a static class) — declarations, not uses;
- property misses: camelCase properties (`name`, `data`, `state`); a bare
  camelCase identifier is indistinguishable from a local by name, so it is
  not counted (by design).

Gates: static class, enum, class, interface and struct recall ≥ 90%.

Map section of the same run: the default map no longer contains the flagged
BCL/Unity collisions (`TryGetValue`, `Action`, `Empty`, `GetBytes`,
`IsNullOrEmpty`, `ToArray`, `Exception`; `Random` remains — Unity's
`Random.Range` names both halves of the repo's `Range.Random`) and no files
under `~` folders; in `map --mention WindowsManager`, direct neighbors of the
type went from 9 of 44 files to 13 of 33 (most of the rest are its
two-hop ExWindows neighborhood).

Output size and latency on the same corpus:

| | Before | After |
|---|---:|---:|
| `refs ExLog` (20 files shown) | 179 chars (0 files) | 2,425 chars, 207 files / 731 lines |
| `refs WindowsManager` | 1,194 chars (no lines) | 1,326 chars with lines |
| `outline`, all C# files, vs reading them | 8.2× smaller | 6.7× smaller (fields/events listed) |
| `outline` of a 2.3k-line DTO file | 10.6k chars | 15.3k chars (268 fields as 36 name lines) |
| `slice` of one method, 360-line file | 3.5k chars | 1.0k chars |
| `refs` / `outline` wall time (process, incl. refresh) | 0.13 s | 0.13 s |
| cold index | 2.2 s | 2.5 s |

## Unity Game Corpus: Chop Chop

`benchmarks/bench_unity_game.py` fetches Unity Open Project #1 "Chop Chop"
(Apache-2.0) at commit `608eac98df29` — text files only (sparse checkout,
blob filter: 337 MB, ~20 s, instead of ~2 GB with textures, audio and
models; `--project PATH` reuses a checkout). It builds truth from the assets:
`.cs.meta` GUIDs → scripts; `m_Script` GUIDs in prefabs, scenes and
ScriptableObject assets → which assets use a script; UnityEvent persistent
calls (`m_MethodName` + `m_TargetAssemblyTypeName`) and animation events
(`functionName` in `.anim` and model `.meta` import settings) → methods
called from data. Spot-checked against grep (CameraManager: 17 asset users
both ways; 39 distinct UnityEvent method names, 24 animation-event names).

Run 2026-09-10 (lotsman 1.7 branch, before U1): 314 C# files, 1,240 asset
files (656 `.asset`, 483 `.prefab`, 50 `.unity`, 22 `.anim`, …).

| Serialized reference | Truth | Shown by `refs` |
|---|---:|---:|
| scripts used by assets (`m_Script`) | 218 | 0 |
| script → asset links | 764 | 0 |
| methods called by UnityEvents | 41 | 0 |
| methods called by animation events | 24 | 0 |

107 scripts used by assets have no code use `refs` can find outside their
own file (TimeElapsedConditionSO in 19 assets, CameraManager in 17) — an
"unused" check from code alone would flag them (the X1 trap). 19 of the 41
UnityEvent calls carry no `m_TargetAssemblyTypeName` (owner only through the
target object's `m_Script`).

Code side on the same corpus (`bench_unity.py --root …/Assets`): refs recall
class 99.8%, method 98.5%, property 92.0%, enum/interface/struct/static
class 100%, precision ≥ 99%; gates pass. Cold index 0.4 s.

Run 2026-09-11 (after U1: assets and `.meta` GUIDs indexed). The truth now
splits UnityEvent calls by target: project code (18), stale — the target
type has no such method (7), no target object (2); the rest call Unity or
package types and GameObjects, which `refs` does not list.

| Serialized reference | Truth | Shown | By |
|---|---:|---:|---|
| scripts used by assets (`m_Script`) | 218 | 218 | `refs` |
| script → asset links | 764 | 764 | `refs` |
| UnityEvent calls to project methods | 18 | 18 | `refs` / `change` |
| animation-event methods (defined) | 24 | 24 | `refs` |
| stale UnityEvent calls (method gone) | 7 | 7 | `unity missing` |
| UnityEvent calls without a target | 2 | 2 | `unity missing` |

Script links lotsman lists that the truth lacks: 0. All 107 X1-trap scripts
now show their asset users in `refs`. UnityEvent calls set by prefab-instance
overrides (added with X1): 12 methods, `refs` shows 11 — the 12th names
`UIPopupButtonSetter`, a type no longer in the project. Gates: script links ≥ 95%, project
UnityEvent calls ≥ 90%. Manual check (the U1 done criterion): 10 random
scripts — asset users identical to grep of their `m_Script` GUID; 10 random
UnityEvent/animation-event sites — each `file:line` is the `m_MethodName` /
`functionName` line, 3 owners traced by hand. Cost: cold index 0.4 → 2.6 s
(1,240 assets, 332 MB of YAML; the private Unity project +3%, 3.96 → 4.09 s);
`refs` 0.12–0.18 s; `unity missing` 0.15 s.

## Unused Candidates (X1)

`lotsman unused` against "no code identifier outside its own body" (what
`refs` alone gives), 2026-09-11. False positives = candidates something does
use or run, judged by hand with grep over the whole project (code with
comments marked, assets, JSON, docs) and script GUIDs.

| Corpus | Baseline | `unused` | Sampled | Used after all (before the fix it prompted) |
|---|---:|---:|---|---|
| Chop Chop | 391 | 108 | 20 baseline; all 116 of the first run | baseline 14/20; `unused` 6/116 (`_underscore` properties) → 0 |
| Private Unity project | 2,969 | 160 | 20 baseline; 5 × 30 fresh random | baseline ≥ 17/20; `unused` 6, 3, 1, 3, 1 of 30 |

What the samples found and the rule each became: Json.NET `ShouldSerializeX`,
`IsExternalInit`, `TypeCache.GetTypesDerivedFrom<T>()` modules, NUnit
`[TestCase]` spanning lines or holding `[0]` in a string, a type looked up by
a tail of its name, a class needed for its nested type, `-executeMethod`
entry points, an Android build callback. `bench_unity_game.py` gates that
`unused` lists nothing the assets use (0 of 108). The rest are dead code
(commented-out callers, lost `[MenuItem]`s, never-thrown exceptions) or
public API nothing here calls (DOTween modules; SDK components for games).
Time: 0.2 s and 0.8 s.

## Early Recommendations

These are recommendations from the current corpus, not final language rankings.

| Repo shape | Expected effect | Why |
|---|---|---|
| Large frameworks with long central files | Highest token savings | `outline` + line-slice reading avoids carrying huge files |
| CMS / app packages with copied dependencies | High after ignore hygiene | `.lotsmanignore` keeps maps focused on project code |
| TS monorepos with fixtures/playgrounds | Medium-high, but exact names may need `defs` | `search` finds concepts; duplicate fixture symbols can outrank core definitions |
| Rust workspaces with long core files | High on navigation slices | crates split concepts, while some files remain long enough for slice savings |
| Small Go-style libraries | Lower token ratio, very fast latency | Files are already compact, but `map/search/defs` still remove lookup churn |
| Vendor-heavy monorepos | Unreliable until ignored | ranking gets diluted by generated/copied code |
| Reflection/DI/serialized-reference-heavy systems | Useful for candidate discovery only | `refs`/`impact` are name-based, not runtime/type complete |

Before making stronger claims by language, add at least: a Java service and a
public C# project with tests (`bench_unity.py` runs on any Unity `Assets/`). Each needs both speed metrics and quality gates,
not just token savings.

## Live behavioral test

Beyond synthetic numbers: in a live Claude Code session on a Unity project
(2008 C# files), the agent answered a who-uses-what impact question with
**4 lotsman tool calls and a single 15-line file read** in 31 seconds, instead
of a grep-and-read cascade. See the map-quality baseline in
[DESIGN.md](DESIGN.md): on Django the top of the map must contain
`cached_property`, `ValidationError`, `ForeignKey` — generic names like
`value` or `list` appearing there indicate a ranking regression.
