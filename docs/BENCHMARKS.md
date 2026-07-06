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
| C# / Unity project | C# game/tooling repo | method-heavy classes, serialized references that lotsman cannot see | live-session evidence; reproducible public corpus still needed |

WordPress release archives include bundled libraries and large JS/admin assets.
The WordPress harness writes a fixture-local `.lotsmanignore` so the measured
perimeter is **WordPress PHP core**, not third-party package ranking. This is
intentional: Lotsman's docs recommend ignoring vendored/generated trees in real
projects too.

## Django Reference Run

Machine: MacBook (Apple Silicon), Python 3.14, lotsman 1.1.0, `[embeddings]`
extra installed.

| Operation | Time |
|---|---|
| Cold index (2272 files, ~41k symbols) | 3.9 s |
| Embedding pass (all symbols) | 1.5 s |
| No-op reindex | 0.12 s |
| Reindex after a 1-file edit | 0.13 s |
| `map` — cold (computes PageRank) | 0.99 s |
| `map` — warm (rank cache) | **0.07 s** |
| `search` (hybrid) | 0.55 s |

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

Machine: MacBook (Apple Silicon), Python 3.14, lotsman 1.1.0, `[embeddings]`
extra installed.

| Operation | Time |
|---|---|
| Cold index (839 files, ~9k symbols) | 2.2 s |
| Embedding pass (all symbols) | 0.5 s |
| No-op reindex | 0.05 s |
| Reindex after a 1-file edit | 0.10 s |
| `map` — cold (computes PageRank) | 0.34 s |
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

Machine: MacBook (Apple Silicon), Python 3.14, lotsman 1.1.0, `[embeddings]`
extra installed.

| Operation | Time |
|---|---|
| Cold index (1131 files, ~2.2k symbols) | 0.46 s |
| Embedding pass (all symbols) | 0.50 s |
| No-op reindex | 0.04 s |
| Reindex after a 1-file edit | 0.05 s |
| `map` — cold (computes PageRank) | 0.09 s |
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

Machine: MacBook (Apple Silicon), Python 3.14, lotsman 1.1.0, `[embeddings]`
extra installed.

| Operation | Time |
|---|---|
| Cold index (92 files, ~1.2k symbols) | 0.16 s |
| Embedding pass (all symbols) | 0.56 s |
| No-op reindex | 0.02 s |
| Reindex after a 1-file edit | 0.02 s |
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

Machine: MacBook (Apple Silicon), Python 3.14, lotsman 1.1.0, `[embeddings]`
extra installed.

| Operation | Time |
|---|---|
| Cold index (101 files, ~3.2k symbols) | 0.36 s |
| Embedding pass (all symbols) | 0.59 s |
| No-op reindex | 0.02 s |
| Reindex after a 1-file edit | 0.02 s |
| `map` — cold (computes PageRank) | 0.02 s |
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
public C# project with tests. Each needs both speed metrics and quality gates,
not just token savings.

## Live behavioral test

Beyond synthetic numbers: in a live Claude Code session on a Unity project
(2008 C# files), the agent answered a who-uses-what impact question with
**4 lotsman tool calls and a single 15-line file read** in 31 seconds, instead
of a grep-and-read cascade. See the map-quality baseline in
[DESIGN.md](DESIGN.md): on Django the top of the map must contain
`cached_property`, `ValidationError`, `ForeignKey` — generic names like
`value` or `list` appearing there indicate a ranking regression.
