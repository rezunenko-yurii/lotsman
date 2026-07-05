# Lotsman — agent instructions

## Code navigation: use lotsman itself (dogfood)

This project IS lotsman. Use it before reading files:

1. Unfamiliar territory → `python3 -m lotsman map --budget 1500 --mention <identifier>`
2. "Where is the code that does X?" → `python3 -m lotsman search "X"` instead of grep chains
3. "What's in this file?" → `python3 -m lotsman outline <file>`, then read only the range
4. "Who uses / where defined?" → `python3 -m lotsman refs <name>` / `defs <name>`
5. Before editing shared code → `python3 -m lotsman impact <files>`
6. Pass files already in context via `--focus`
7. Read a whole file only after outline/search confirmed it's needed

MCP alternative: server declared in `.mcp.json` (tools map/search/outline/defs/refs/impact).

## Project rules

- Design rationale: `docs/DESIGN.md`; history: `docs/DEVLOG.md`; numbers: `docs/BENCHMARKS.md`
- Tests: `python3 -m unittest discover -s tests` — mandatory before commit
- Dependencies: tree-sitter + tree-sitter-language-pack; model2vec is optional —
  all code must degrade without it (see `embed.available()`)
- Changing extraction/schema semantics → bump `INDEX_VERSION` in `indexer.py`
- Changing ranking → re-check map quality via `benchmarks/bench_django.py`;
  baseline: `cached_property`, `ValidationError`, `ForeignKey` at the top,
  no generic names like `value`/`list`
- Language support changes → keep the fixture tests (C#/TS/Go/Python) green;
  C# guards the Unity battleground
- Positioning: cheap local navigator, NOT a semantic code-intelligence engine.
  `refs`/`impact` are name-based heuristics — keep the docs honest about it
