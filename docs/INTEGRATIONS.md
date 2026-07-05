# Hooking Lotsman up to AI agents

## Install

```bash
pip install "lotsman[embeddings] @ git+https://github.com/rezunenko-yurii/lotsman"
# or from a checkout: pip install -e ".[embeddings]"
```

## One-command onboarding

From the root of any repository:

```bash
lotsman init                     # universal: AGENTS.md policy, .lotsmanignore,
                                 # .gitignore entry, first index + warm cache
lotsman init --agent claude      # + .mcp.json and a CLAUDE.md -> AGENTS.md pointer
lotsman init --agent cursor      # + .cursor/mcp.json
lotsman init --agent codex       # prints the one-time global registration command
```

`init` is idempotent: the policy lives between `<!-- lotsman:policy:begin/end -->`
markers and is refreshed in place; existing content in AGENTS.md, .mcp.json and
CLAUDE.md is preserved.

Two integration channels exist and they are complementary:

- **Policy (AGENTS.md)** — works with *any* agent that can run shell commands
  (Codex, Claude Code, opencode, aider, ...). Zero infrastructure.
- **MCP server** — typed tools with schemas and budget parameters; no shell
  quoting, no output parsing. Worth it for agents you use daily.

## Per-agent notes

### Claude Code (verified: 2.1.150)

`lotsman init --agent claude` writes `.mcp.json` and points CLAUDE.md at
AGENTS.md (one source of truth via the `@AGENTS.md` import). Optional but
recommended — session-start map injection in `.claude/settings.json`:

```json
{"hooks": {"SessionStart": [{"matcher": "startup|clear", "hooks": [{
  "type": "command",
  "command": "echo '## Repo map (lotsman):'; lotsman map --budget 1200 2>/dev/null"
}]}]}}
```

The agent then starts every session already holding the repo map (~0.1–0.3 s
with a warm rank cache) and never needs to ask for it.

### Codex CLI

Codex reads `AGENTS.md` automatically — the policy channel needs nothing else.
For MCP tools, register **once, globally** (Codex reads `~/.codex/config.toml`;
project-local `.codex/` files are not picked up):

```bash
codex mcp add lotsman -- lotsman --repo . mcp
```

`--repo .` resolves against the server's working directory at launch, so one
global registration serves every project you open. Approval tip: lotsman tools
are read-only navigation — per-tool `approval_mode = "approve"` will prompt on
every call and slow the agent down; consider relaxing it for `map/search/
outline/defs/refs`.

### Cursor

`lotsman init --agent cursor` writes `.cursor/mcp.json`. Put the policy into
`.cursor/rules/` or keep it in AGENTS.md (Cursor reads AGENTS.md since 1.6).

### Any other CLI agent

If it can run shell commands, the AGENTS.md policy is enough. The JSON outputs
(`search/outline/defs/refs/index --json`, `doctor --json`) are stable and meant
for scripting.

## Lifehacks

1. **Warm the cache after every merge/pull** — `lotsman index && lotsman map
   >/dev/null` in a post-merge hook or CI step. First agent query becomes
   instant instead of ~1 s.
2. **`--mention` is the highest-leverage flag.** `lotsman map --mention
   JobQueue` biases the whole PageRank toward the task at hand — the map stops
   being generic and starts being *about your task*.
3. **`--focus` for files already in context** — the map shows their
   dependencies instead of repeating what the agent already sees.
4. **`doctor --json --fail-on-warn` as a pre-flight gate** — in CI or at agent
   session start. A stale index silently feeding an agent wrong line numbers
   is the worst failure mode; this makes it loud.
5. **`.lotsmanignore` before first impressions.** On Unity/monorepo projects,
   exclude `Plugins/`, `vendor/`, generated code *before* showing the map to
   an agent — otherwise the top of the map is somebody else's SDK.
6. **`impact` before and after a refactor batch** — before: what am I about to
   touch; after: what to re-check. Treat it as a candidate list, not proof.
7. **One source of truth for instructions.** Keep the policy in AGENTS.md;
   make CLAUDE.md a one-line `@AGENTS.md` import. Duplicated policies drift.
8. **Monorepos:** index components separately (`lotsman --repo services/api`)
   — tighter maps, cheaper personalization, independent caches.
