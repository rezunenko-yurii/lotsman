# Benchmarks

All numbers are produced by the reproducible harness in
[`benchmarks/bench_django.py`](../benchmarks/bench_django.py). It shallow-clones
Django at a **pinned tag (`5.2`)**, builds the index from scratch, and measures
every operation plus a realistic agent navigation scenario.

```bash
python benchmarks/bench_django.py                     # clones Django itself
python benchmarks/bench_django.py --django-dir ~/src/django   # reuse a checkout
```

## Reference run

Machine: MacBook (Apple Silicon), Python 3.14, lotsman 1.1.0, `[embeddings]`
extra installed.

| Operation | Time |
|---|---|
| Cold index (2272 files, ~43k symbols) | 3.7 s |
| No-op reindex | 0.12 s |
| Reindex after a 1-file edit | 0.14 s |
| `map` — cold (computes PageRank) | 0.99 s |
| `map` — warm (rank cache) | **0.05 s** |
| `search` (hybrid) | 1.0 s |

## Token-cost scenario

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

## Live behavioral test

Beyond synthetic numbers: in a live Claude Code session on a Unity project
(2008 C# files), the agent answered a who-uses-what impact question with
**4 lotsman tool calls and a single 15-line file read** in 31 seconds, instead
of a grep-and-read cascade. See the map-quality baseline in
[DESIGN.md](DESIGN.md): on Django the top of the map must contain
`cached_property`, `ValidationError`, `ForeignKey` — generic names like
`value` or `list` appearing there indicate a ranking regression.
