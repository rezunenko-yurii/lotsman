"""`lotsman init` — one-command onboarding for a repository.

Universal steps (always):
- write/refresh the navigation policy in AGENTS.md (marker-delimited, idempotent);
- create a commented .lotsmanignore skeleton if absent;
- add .lotsman/ to .gitignore when the repo has one;
- build the index and warm the rank cache so the first agent query is instant.

Per-agent config (opt-in via --agent):
- claude: .mcp.json entry + CLAUDE.md pointer importing AGENTS.md;
- cursor: .cursor/mcp.json entry;
- codex: prints the global registration command (Codex reads
  ~/.codex/config.toml, not project files — we never edit user-global config).
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


def run_init(root: Path, agents: list[str], no_index: bool = False) -> int:
    cmd, argv, env = _invocation()
    say = lambda s: print(f"[init] {s}")  # noqa: E731

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
        elif agent == "cursor":
            if _merge_mcp_json(root / ".cursor" / "mcp.json", argv, env):
                say("cursor: registered MCP server in .cursor/mcp.json")
        elif agent == "codex":
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
