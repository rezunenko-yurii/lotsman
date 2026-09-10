#!/usr/bin/env python3
"""Question-set benchmark: what an agent pays to answer real project questions.

For every question (docs, change safety, config data, assembly/platform,
compile errors) the harness executes scripted *plans* — the tool calls an
agent would make on each path — and records:

  tokens  everything the agent saw (command output, lines read), chars / 4;
  calls   number of tool calls;
  found   whether the expected answer was in what it saw:
            section     ≥ 50% of the section's lines were read (knowing where
                        the answer is does not answer the question);
            files       share of expected files that surfaced in any output or
                        read (optional files ignored); `noise` = other source
                        files that surfaced;
            file_lines  share of expected (file, line) locations surfaced;
            value       the evidence location was read or shown.

Plans per question, e.g. `baseline` (grep + reads, no lotsman) and `lotsman16`
(an agent following the lotsman policy today, falling back to grep where
lotsman has nothing). A plan is a list of steps:

  ["grep", ROOT, *grep_args]                     e.g. ["grep", "assets", "-rl", "X", "--include=*.md", "."]
  ["grep", ROOT, *grep_args, {"drop": "/Api/"}]  drop output lines containing a substring
  ["read", ROOT, path] / ["read", ROOT, path, first, last]
  ["read_hits", context]                         open every hit of the previous grep: ±context
                                                 lines around -n hits, whole file for -l hits
  ["lotsman", *cli_args]                         `python -m lotsman --repo ASSETS ...`
  ["ls" | "find", ROOT, *args]
  ["stat", ROOT, path, ...]
  ["python", code]                               a one-off script the agent would write;
                                                 its source counts as tokens too
  ["read_doc_hits", k]                           read the ranges of the first k `docs:` hits
                                                 of the previous lotsman search
  ["until_found"]                                the agent recognizes an answer it has seen:
                                                 later steps (a fallback) run only if the
                                                 expected answer is not reached yet
ROOT is "assets" or "project"; `EDITOR_LOG` in any argument is replaced by the
fixture log path. Only these operations run — plans never reach a shell.

Questions and plans about private corpora live outside the repository (see
`--questions`, `--plans`); this file is generic.

Usage:
    python benchmarks/bench_unity_questions.py --project PATH \
        --questions 'benchmarks/private/q_*.json' --plans benchmarks/private/plans.json \
        [--paths baseline,lotsman16] [--only docs-01,...] [--json OUT] [--show ID]
"""

from __future__ import annotations

import argparse
import glob
import hashlib
import json
import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SOURCE_EXT = r"(?:cs|md|json|rc|asmdef|prefab|unity|asset|txt|meta|csproj)"
PATH_RE = re.compile(r"((?:[\w.~\-]+/)*[\w.~\-]+\." + SOURCE_EXT + r")"
                     r"(?:[:(](\d+(?:,\d+)*))?")
GREP_LINE_RE = re.compile(r"^([^:\s][^:]*?\." + SOURCE_EXT + r")(?:[:-](\d+))?(?:[:-]|$)")
# What an agent actually sees: Claude Code truncates command output at about
# 30k characters and the Read tool stops at 2000 lines. Anything beyond is
# neither paid for nor seen.
MAX_OUTPUT = 30_000
MAX_READ_LINES = 2000
MAX_LINE = 2000  # longer lines are cut (a 2 MB one-line JSON would otherwise dominate)
A_ROOT = "assets"


@dataclass
class Seen:
    """What the agent saw: read spans and mentioned (path, line) locations,
    all normalised to project-relative paths."""
    spans: dict[str, list[tuple[int, int]]] = field(default_factory=dict)
    mentions: dict[str, set[int]] = field(default_factory=dict)  # 0 = path only
    stats: set[str] = field(default_factory=set)

    def read(self, path: str, first: int, last: int) -> None:
        self.spans.setdefault(path, []).append((first, last))

    def mention(self, path: str, line: int = 0) -> None:
        self.mentions.setdefault(path, set()).add(line)

    def surfaced(self) -> set[str]:
        return set(self.spans) | set(self.mentions)

    def covered(self, path: str, line: int) -> bool:
        return (any(a <= line <= b for a, b in self.spans.get(path, ()))
                or line in self.mentions.get(path, ()))


class Runner:
    def __init__(self, project: Path, assets: Path, editor_log: Path | None,
                 index: Path | None = None):
        self.project, self.assets, self.editor_log = project, assets, editor_log
        self.index = index
        self.assets_prefix = assets.relative_to(project).as_posix() + "/"

    # --- path normalisation --------------------------------------------------

    def rel(self, path: str, root: str) -> str:
        """Project-relative path for a path printed or read under ROOT."""
        path = path.lstrip("./")
        if path.startswith(self.assets_prefix) or root == "project":
            return path
        return self.assets_prefix + path

    def base(self, root: str) -> Path:
        return self.assets if root == "assets" else self.project

    def sub(self, arg):
        return (arg.replace("EDITOR_LOG", str(self.editor_log))
                if isinstance(arg, str) and self.editor_log else arg)

    # --- execution -----------------------------------------------------------

    def run(self, plan: list, is_found=None) -> tuple[int, int, Seen, list[str]]:
        seen, log, chars, calls = Seen(), [], 0, 0
        last_hits: list[tuple[str, int]] = []  # (project path, line or 0)
        last_lotsman = ""
        for step in plan:
            op, args = step[0], [self.sub(a) for a in step[1:]]
            out = ""
            if op == "until_found":
                if is_found and is_found(seen):
                    log.append("until_found: answer already seen, fallback skipped")
                    break
                continue
            calls += 1
            if op in ("grep", "find", "ls"):
                root, rest = args[0], args[1:]
                drop = None
                if rest and isinstance(rest[-1], dict):
                    drop = rest[-1].get("drop")
                    rest = rest[:-1]
                out = self._proc([op, *rest], self.base(root))
                if drop:
                    out = "".join(line for line in out.splitlines(True) if drop not in line)
                last_hits = self._collect(out, root, seen, from_grep=(op == "grep"))
            elif op == "read":
                out = self._read(args[0], args[1], args[2:] or None, seen)
            elif op == "read_hits":
                ctx = int(args[0])
                parts = []
                for path, line in last_hits:
                    rng = None if line == 0 else [max(1, line - ctx), line + ctx]
                    parts.append(self._read("project", path, rng, seen))
                out = "".join(parts)
            elif op == "lotsman":
                env = {**os.environ, "PYTHONPATH": str(REPO),
                       "LOTSMAN_QUERYLOG": "0"}  # never pollute the project's log
                if self.index:  # keep the project's own index untouched
                    env["LOTSMAN_INDEX"] = str(self.index)
                out = self._proc([sys.executable, "-m", "lotsman", "--repo",
                                  str(self.assets), *args], REPO, env=env)
                self._collect(out, "assets", seen, from_grep=False)
                last_lotsman = out
            elif op == "read_doc_hits":
                ranges = re.findall(r"^  (\S+\.md):(\d+)-(\d+)  ", 
                                    last_lotsman.split("\ndocs:\n", 1)[-1]
                                    if "\ndocs:\n" in last_lotsman else "", re.M)
                out = "".join(self._read(A_ROOT, p, [a, b], seen)
                              for p, a, b in ranges[:int(args[0])])
            elif op == "stat":
                root, paths = args[0], args[1:]
                lines = []
                for p in paths:
                    full = Path(p) if os.path.isabs(p) else self.base(root) / p
                    lines.append(f"{p}  mtime={time.ctime(full.stat().st_mtime)}")
                    seen.stats.add(str(p))
                out = "\n".join(lines) + "\n"
            elif op == "python":
                code = args[0]
                chars += len(code)  # the agent writes it
                out = self._proc([sys.executable, "-c", code], self.project)
                self._collect(out, "project", seen, from_grep=False)
            else:
                raise ValueError(f"unknown op {op!r}")
            chars += len(out)
            log.append(f"{op} {' '.join(str(a) for a in args)[:120]} -> {len(out)} chars")
        return chars // 4, calls, seen, log

    def _proc(self, cmd: list[str], cwd: Path, env=None) -> str:
        res = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True,
                             errors="replace", env=env, timeout=300)
        return _clip_lines(res.stdout)[:MAX_OUTPUT]

    def _read(self, root: str, path: str, rng, seen: Seen) -> str:
        rel = self.rel(path, root)
        full = self.project / rel
        try:
            lines = full.read_text(encoding="utf-8", errors="replace").splitlines(True)
        except OSError:
            return ""
        first, last = ((1, min(len(lines), MAX_READ_LINES)) if not rng
                       else (int(rng[0]), min(int(rng[1]), len(lines))))
        seen.read(rel, first, last)
        return _clip_lines("".join(lines[first - 1:last]))

    def _collect(self, out: str, root: str, seen: Seen, from_grep: bool):
        hits = []
        for line in out.splitlines():
            if from_grep:
                # `path:12:text`, `path-13-context` (-A/-B), `path` (-l)
                m = GREP_LINE_RE.match(line)
                if m:
                    rel = self.rel(m.group(1), root)
                    ln = int(m.group(2)) if m.group(2) else 0
                    seen.mention(rel, ln)
                    if not line[m.end(1):].startswith("-"):  # context lines are not hits
                        hits.append((rel, ln))
                    continue
                # single-file grep prints `12:text`; paths inside the text
                # (compiler output `Assets/X.cs(217,16)`) still count below
            for m in PATH_RE.finditer(line):
                rel = self.rel(m.group(1), root)
                seen.mention(rel, 0)
                for ln in (m.group(2) or "").split(","):
                    if ln:
                        seen.mention(rel, int(ln))
        return hits


def _clip_lines(text: str) -> str:
    return "".join(line if len(line) <= MAX_LINE else line[:MAX_LINE] + "\n"
                   for line in text.splitlines(True))


# --- scoring -------------------------------------------------------------------

def _proj(path: str, root: str, assets_prefix: str) -> str:
    return path if root == "project" or path.startswith(assets_prefix) else assets_prefix + path


def score(q: dict, seen: Seen, assets_prefix: str) -> tuple[float, int]:
    """(found in [0,1], noise files) for one question."""
    e = q["expected"]
    root = e.get("root", "Assets").lower()
    root = "project" if root == "project" else "assets"
    kind = e["type"]
    if kind == "section":
        best = 0.0
        for sec in [e, *q.get("also_ok", [])]:
            path = _proj(sec["path"], root, assets_prefix)
            a, b = sec["lines"]
            total = b - a + 1
            read = sum(1 for n in range(a, b + 1)
                       if any(x <= n <= y for x, y in seen.spans.get(path, ())))
            best = max(best, 1.0 if read * 2 >= total else 0.0)
        return best, 0
    if kind == "files":
        expected = {_proj(p, root, assets_prefix) for p in e["paths"]}
        optional = {_proj(p, root, assets_prefix) for p in e.get("optional_paths", [])}
        extra = {_proj(e["definition"].split(":")[0], root, assets_prefix)} if e.get("definition") else set()
        surfaced = {p for p in seen.surfaced() if re.search(r"\.(cs|json|rc|asmdef|prefab|unity|asset)$", p)}
        found = len(expected & surfaced) / len(expected)
        noise = len(surfaced - expected - optional - extra)
        return found, noise
    if kind == "file_lines":
        items = e.get("items") or [{"path": e["path"], "lines": e["lines"]}]
        locs = [(_proj(it["path"], root, assets_prefix), ln) for it in items for ln in it["lines"]]
        return sum(seen.covered(p, ln) for p, ln in locs) / len(locs), 0
    if kind == "value":
        ev = e.get("evidence")
        if not ev:  # staleness-type answers: both files must have been stat-ed
            return (1.0 if len(seen.stats) >= 2 else 0.0), 0
        path, _, line = ev.rpartition(":")
        path = path if path.startswith(("Assets/", "ProjectSettings/")) else _proj(path, root, assets_prefix)
        return (1.0 if seen.covered(path, int(line)) else 0.0), 0
    raise ValueError(kind)


def check_pins(q: dict, project: Path, assets: Path) -> list[str]:
    stale = []
    for path, sha in q.get("verified", {}).items():
        full = next((c for c in (project / path, assets / path) if c.is_file()), None)
        if full is None or hashlib.sha256(full.read_bytes()).hexdigest()[:12] != sha:
            stale.append(path)
    return stale


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--project", required=True, help="Unity project root")
    ap.add_argument("--assets", default="Assets", help="indexed subfolder (default Assets)")
    ap.add_argument("--questions", required=True, help="glob of question JSON files")
    ap.add_argument("--plans", required=True, help="plans JSON: {id: {path_name: [steps]}}")
    ap.add_argument("--paths", default="baseline,lotsman16")
    ap.add_argument("--only", help="comma-separated question ids")
    ap.add_argument("--index", help="index file for lotsman calls (LOTSMAN_INDEX); "
                                    "keeps the project's own index untouched")
    ap.add_argument("--json", help="write per-question results here")
    ap.add_argument("--show", help="print the step log of one question id")
    args = ap.parse_args()

    project = Path(args.project).resolve()
    assets = project / args.assets
    questions = [q for f in sorted(glob.glob(args.questions)) for q in json.loads(Path(f).read_text())]
    plans = json.loads(Path(args.plans).read_text())
    only = set(args.only.split(",")) if args.only else None
    path_names = args.paths.split(",")
    prefix = assets.relative_to(project).as_posix() + "/"

    rows, results = [], {}
    for q in questions:
        if only and q["id"] not in only:
            continue
        fixture = (q.get("fixture") or {}).get("editor_log")
        log_path = (REPO / fixture) if fixture else None
        if log_path:  # staleness scenarios depend on the log's mtime
            edited = assets / "ExFramework/Sdk/GameSdk/ExWindows/Runtime/WindowsManager.cs"
            when = (edited.stat().st_mtime - 3600) if "older" in q["fixture"].get("mtime", "") \
                else time.time()
            os.utime(log_path, (when, when))
        runner = Runner(project, assets, log_path,
                        Path(args.index).resolve() if args.index else None)
        stale = check_pins(q, project, assets)
        res = {"category": q["category"], "stale": stale}
        for name in path_names:
            plan = plans.get(q["id"], {}).get(name)
            if plan is None:
                continue
            tokens, calls, seen, log = runner.run(
                plan, is_found=lambda s, q=q: score(q, s, prefix)[0] >= 1.0)
            found, noise = score(q, seen, prefix)
            res[name] = {"tokens": tokens, "calls": calls, "found": round(found, 3),
                         "noise": noise}
            if args.show == q["id"]:
                print(f"--- {q['id']} / {name}")
                print("\n".join("  " + line for line in log))
        results[q["id"]] = res
        rows.append(q["id"])

    # per-question table
    hdr = f"{'id':<11}" + "".join(f"{n + ' tok':>16}{'calls':>6}{'found':>6}{'noise':>6}" for n in path_names)
    print(hdr)
    for qid in rows:
        r = results[qid]
        line = f"{qid:<11}"
        for n in path_names:
            x = r.get(n)
            line += (f"{x['tokens']:>16,}{x['calls']:>6}{x['found']:>6.2f}{x['noise']:>6}"
                     if x else f"{'—':>16}{'':>18}")
        if r["stale"]:
            line += "  STALE: " + ", ".join(r["stale"])
        print(line)

    # per-category totals
    print()
    cats = sorted({r["category"] for r in results.values()})
    print(f"{'category':<11}" + "".join(f"{n + ' tok':>16}{'calls':>6}{'found':>6}" for n in path_names))
    for cat in cats + ["ALL"]:
        ids = [i for i in rows if cat == "ALL" or results[i]["category"] == cat]
        line = f"{cat:<11}"
        for n in path_names:
            xs = [results[i][n] for i in ids if n in results[i]]
            if xs:
                line += (f"{sum(x['tokens'] for x in xs):>16,}{sum(x['calls'] for x in xs):>6}"
                         f"{sum(x['found'] for x in xs) / len(xs):>6.2f}")
        print(line)

    if args.json:
        Path(args.json).write_text(json.dumps(results, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
