"""`lotsman init` — one-command onboarding for a repository.

Universal steps (always):
- write/refresh the navigation policy in AGENTS.md (marker-delimited, idempotent);
- create a commented .lotsmanignore skeleton if absent;
- add .lotsman/ to .gitignore when the repo has one;
- build the index and warm the rank cache so the first agent query is instant.

Per-agent config (opt-in via --agent):
- claude: .mcp.json entry + CLAUDE.md pointer + Claude skill;
- cursor: .cursor/mcp.json entry;
- codex: Codex skill + global registration command (Codex reads
  ~/.codex/config.toml for MCP; we never edit user-global config).
"""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

MARK_BEGIN = "<!-- lotsman:policy:begin -->"
MARK_END = "<!-- lotsman:policy:end -->"

POLICY = """{begin}
## Code navigation: lotsman

Use `{cmd}` before reading files — it is cheaper and faster than reading:
Lotsman is a deep local index plus narrow retrieval layer: ask for the next
small slice, not the whole project.

1. New task in unfamiliar territory -> `{cmd} map --budget 1500 --mention <identifier>`
2. "Where is the code that does X?"  -> `{cmd} search "X"` instead of grep chains
3. "What's in this file?"            -> `{cmd} outline <file>`, then read only that line range
4. "Who uses / where is it defined?" -> `{cmd} refs <name>` / `{cmd} defs <name>`
5. Before editing shared code        -> `{cmd} impact <files>` — heuristic: candidates to check, not proof
6. Read a whole file only after outline/search confirmed it is the right one.

The index lives in `.lotsman/` and updates incrementally. `{cmd} doctor --json` shows health.
{end}"""

IGNORE_SKELETON = """\
# lotsman ignore rules (gitignore-lite globs; `dir/` matches the whole subtree)
# Exclude vendored/third-party code so map and search show YOUR code:
# Plugins/
# vendor/
# third_party/
# *.gen.cs
"""

CODEX_SKILL = """\
---
name: lotsman-navigation
description: Use when an AI coding agent needs to navigate, understand, search, inspect, or safely edit a codebase with Lotsman available; especially unfamiliar code, behavior search, symbol/file inspection, definitions/references, impact analysis, affected-test selection, or avoiding broad reads.
---

# Lotsman Navigation

## Core Model

Deep local index, narrow retrieval. Lotsman indexes the repo deeply in
`.lotsman/index.db`, but the agent should request only the next useful slice.
Do not confuse a larger `map --budget` with deeper indexing.

Prefer MCP tools when available. Tool names vary by host: some agents expose
plain `map/search/outline/...`, while others expose names like
`mcp__lotsman__map`. Fall back to CLI commands when MCP is unavailable.

## Slice Selection

| Situation | Ask for |
|---|---|
| Unfamiliar area | `map` around task terms, budget 1200-1800 |
| Clear concept/domain/API | `map` with `mentions` / `--mention` |
| Behavior or feature search | `search` before grep or broad reads |
| Candidate file found | `outline`, then read only relevant ranges |
| One symbol matters | `slice FILE NAME` instead of reading the whole file |
| Symbol/API change | `refs NAME`; use `refs Class.Method` for noisy method names |
| File or batch change | `impact` before and after editing |
| Need test candidates | `impact --tests` / MCP impact with `tests: true` |
| Need usage telemetry | run MCP with `LOTSMAN_QUERYLOG=1`, then `report` |
| Need broader context | Increase map budget only with mention/focus |

## Rules

- Start compact; widen only when the task demands it.
- Read files only after `search`, `outline`, or `slice` makes them relevant.
- Treat `refs` and `impact` as name-based candidate lists, not proof.
- Use `.lotsman/wiring.json` for project-specific DI/reflection/config names.
- Use `doctor` or `index --verify` when freshness or environment health is in doubt.
"""

CLAUDE_SKILL = """\
---
name: lotsman-navigation
description: Use when Claude Code needs to navigate, understand, search, inspect, or safely edit a codebase with Lotsman available; especially unfamiliar code, behavior search, file inspection, definitions/references, impact analysis, or avoiding broad reads.
allowed-tools: Bash(lotsman:*), Bash(python3 -m lotsman:*), Read, Grep
---

# Lotsman Navigation

## Core Model

Deep local index, narrow retrieval. Lotsman indexes the repo deeply in
`.lotsman/index.db`, but the agent should request only the next useful slice.
Do not confuse a larger `map --budget` with deeper indexing.

Use `lotsman` before broad reads. If only `python3 -m lotsman` works in this
checkout, use that command prefix instead.

## Slice Selection

| Situation | Ask for |
|---|---|
| Unfamiliar area | `lotsman map --budget 1500 --mention <task-term>` |
| Behavior or feature search | `lotsman search "<query>"` |
| Candidate file found | `lotsman outline <file>`, then read only relevant ranges |
| Symbol/API change | `lotsman refs <name>` before editing |
| File or batch change | `lotsman impact <files>` before and after editing |
| Need broader context | Increase map budget only with `--mention` or `--focus` |

## Rules

- Start compact; widen only when the task demands it.
- Read files only after `search` or `outline` makes them relevant.
- Treat `refs` and `impact` as name-based candidate lists, not proof.
- Use `lotsman doctor` or `lotsman index --verify` when freshness or environment health is in doubt.
"""


def _invocation() -> tuple[str, list[str], dict | None]:
    """(display command, argv prefix, extra env) that will actually work here."""
    if shutil.which("lotsman"):
        return "lotsman", ["lotsman"], None
    pkg_parent = str(Path(__file__).resolve().parent.parent)
    return ("python3 -m lotsman", [sys.executable, "-m", "lotsman"],
            {"PYTHONPATH": pkg_parent})


def _upsert_policy(agents_md: Path, cmd: str) -> str:
    block = POLICY.format(begin=MARK_BEGIN, end=MARK_END, cmd=cmd)
    if not agents_md.exists():
        agents_md.write_text(f"# Agent instructions\n\n{block}\n")
        return "created"
    text = agents_md.read_text()
    if MARK_BEGIN in text and MARK_END in text:
        head, rest = text.split(MARK_BEGIN, 1)
        _, tail = rest.split(MARK_END, 1)
        agents_md.write_text(head + block + tail)
        return "refreshed"
    agents_md.write_text(text.rstrip() + f"\n\n{block}\n")
    return "appended"


def _ensure_gitignore(root: Path) -> bool:
    gi = root / ".gitignore"
    if not gi.exists() and not (root / ".git").exists():
        return False
    lines = gi.read_text().splitlines() if gi.exists() else []
    if any(l.strip().rstrip("/") == ".lotsman" for l in lines):
        return False
    lines.append(".lotsman/")
    gi.write_text("\n".join(lines) + "\n")
    return True


def _ensure_lotsmanignore(root: Path) -> bool:
    path = root / ".lotsmanignore"
    if path.exists():
        return False
    path.write_text(IGNORE_SKELETON)
    return True


def _merge_mcp_json(path: Path, argv: list[str], env: dict | None) -> bool:
    """Add the lotsman server to an .mcp.json-style file; keep everything else."""
    config = {}
    if path.exists():
        try:
            config = json.loads(path.read_text())
        except json.JSONDecodeError:
            return False  # never clobber a file we cannot parse
    servers = config.setdefault("mcpServers", {})
    if "lotsman" in servers:
        return False
    entry: dict = {"command": argv[0],
                   "args": argv[1:] + ["--repo", ".", "mcp"]}
    if env:
        entry["env"] = env
    servers["lotsman"] = entry
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(config, indent=2) + "\n")
    return True


def _ensure_claude_pointer(root: Path) -> bool:
    """CLAUDE.md should import AGENTS.md, not duplicate it — one source of truth."""
    path = root / "CLAUDE.md"
    pointer = "@AGENTS.md"
    if not path.exists():
        path.write_text("# Agent instructions\n\nSee @AGENTS.md — the single "
                        "source of agent instructions for this repo.\n")
        return True
    text = path.read_text()
    if pointer in text or MARK_BEGIN in text:
        return False
    path.write_text(text.rstrip()
                    + "\n\n## Code navigation\n\nSee @AGENTS.md for the "
                      "lotsman navigation policy.\n")
    return True


def _write_skill(path: Path, text: str) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and path.read_text() == text:
        return "unchanged"
    status = "refreshed" if path.exists() else "created"
    path.write_text(text)
    return status


def _detect_agents(root: Path) -> list[str]:
    agents = []
    if (root / ".claude" / "skills" / "lotsman-navigation" / "SKILL.md").exists():
        agents.append("claude")
    if (root / ".codex" / "skills" / "lotsman-navigation" / "SKILL.md").exists():
        agents.append("codex")
    if (root / ".cursor" / "mcp.json").exists():
        agents.append("cursor")
    return agents


def run_init(root: Path, agents: list[str], no_index: bool = False,
             label: str = "init") -> int:
    cmd, argv, env = _invocation()
    say = lambda s: print(f"[{label}] {s}")  # noqa: E731

    say(f"policy in AGENTS.md: {_upsert_policy(root / 'AGENTS.md', cmd)}")
    if _ensure_lotsmanignore(root):
        say("created .lotsmanignore skeleton (edit it to exclude vendored code)")
    if _ensure_gitignore(root):
        say("added .lotsman/ to .gitignore")

    for agent in agents:
        if agent == "claude":
            if _merge_mcp_json(root / ".mcp.json", argv, env):
                say("claude: registered MCP server in .mcp.json")
            if _ensure_claude_pointer(root):
                say("claude: CLAUDE.md points to AGENTS.md")
            status = _write_skill(
                root / ".claude" / "skills" / "lotsman-navigation" / "SKILL.md",
                CLAUDE_SKILL)
            say(f"claude: lotsman-navigation skill {status}")
        elif agent == "cursor":
            if _merge_mcp_json(root / ".cursor" / "mcp.json", argv, env):
                say("cursor: registered MCP server in .cursor/mcp.json")
        elif agent == "codex":
            status = _write_skill(
                root / ".codex" / "skills" / "lotsman-navigation" / "SKILL.md",
                CODEX_SKILL)
            say(f"codex: lotsman-navigation skill {status}")
            say("codex reads AGENTS.md automatically; for MCP tools register "
                "once globally:")
            say(f"  codex mcp add lotsman -- {cmd} --repo . mcp")
            say('  (`--repo .` resolves per-project: one registration serves '
                "every repo)")

    if not no_index:
        from lotsman import embed, indexer, repomap
        store = indexer.open_store(root)
        res = indexer.index_repo(root, store)
        embedded = embed.embed_missing(store)
        repomap.generate_map(store)  # warm the rank cache
        store.close()
        say(f"indexed {res.scanned} files ({embedded} symbols embedded), "
            "rank cache warmed")
    say("done — try: " + cmd + " map --budget 1500")
    return 0


def run_update(root: Path, agents: list[str] | None = None,
               no_index: bool = False) -> int:
    selected = list(agents) if agents is not None else _detect_agents(root)
    if agents is None:
        if selected:
            print(f"[update] detected agents: {', '.join(selected)}")
        else:
            print("[update] no existing agent-specific artifacts detected; "
                  "pass --agent to add one")
    return run_init(root, agents=selected, no_index=no_index, label="update")
