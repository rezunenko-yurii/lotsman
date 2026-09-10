"""Compile errors from the Unity Editor log, offline.

The Editor writes compiler output into its log (`Assets/X.cs(12,5): error
CS0103: …`, the same shape from 2019 to 6000). Only the last compilation
counts: earlier errors may be fixed. The log has no per-line timestamps, so
freshness is judged by the file's mtime against the sources' — errors may
predate edits made after the log was last written.

Only compiler lines and the project path are read from the log: its header
also carries command-line secrets (Hub access tokens) that must never be
printed.
"""

from __future__ import annotations

import os
import re
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

from lotsman import csdefines

COMPILE_START_RE = re.compile(
    r"^(?:\[ScriptCompilation\] Requested script compilation"
    r"|Starting: .*ScriptCompilationBuildProgram"
    r"|- Starting script compilation"
    r"|-----CompilerOutput:)")
MESSAGE_RE = re.compile(
    r"^(?P<path>[^\s(][^(]*?\.cs)\((?P<line>\d+),(?P<col>\d+)\): "
    r"(?P<sev>error|warning) (?P<code>[A-Z]+\d+): (?P<msg>.*)$")
BARE_ERROR_RE = re.compile(r"^(?:[^:]*: )?error (?P<code>CS\d+): (?P<msg>.*)$")
VERSION_RE = re.compile(r"^Unity Editor version:\s+(\S+)")
PROJECT_RE = re.compile(r"^Successfully changed project path to: (.+)$")
MAX_SHOWN = 30
MAX_STALE_SHOWN = 5
MAX_MSG = 200


@dataclass
class Message:
    path: str        # as printed in the log, `/` separators
    line: int
    col: int
    severity: str
    code: str
    text: str


@dataclass
class LogReport:
    path: Path
    version: str = ""
    project: str = ""
    compile_line: int = 0            # log line where the last compilation starts
    messages: list[Message] = field(default_factory=list)   # last compilation, deduped
    bare_errors: list[str] = field(default_factory=list)    # errors without a file
    earlier_errors: int = 0          # distinct errors in compilations before the last one


def default_logs(project: Path) -> list[Path]:
    """Where Editor logs live: the project's own `Logs/` first (newer Unity
    versions), then the per-user location of this OS."""
    home = Path.home()
    if sys.platform == "darwin":
        user = home / "Library/Logs/Unity/Editor.log"
    elif os.name == "nt":
        user = Path(os.environ.get("LOCALAPPDATA", home / "AppData/Local")) / "Unity/Editor/Editor.log"
    else:
        user = home / ".config/unity3d/Editor.log"
    return [project / "Logs/Editor.log", user]


def parse(path: Path) -> LogReport:
    rep = LogReport(path, project=parse_project(path))
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    rep.version = next((m.group(1) for t in lines[:50] if (m := VERSION_RE.match(t))), "")
    segments: list[tuple[int, list[str]]] = [(0, [])]
    for n, text in enumerate(lines, 1):
        if COMPILE_START_RE.match(text):
            if segments[-1][1] or segments[-1][0] == 0:
                segments.append((n, []))
            continue
        if ("error" in text or "warning" in text) and (" error " in text or " warning " in text
                                                      or text.startswith(("error ", "warning "))):
            segments[-1][1].append(text)
    # The last compilation is the last segment that printed compiler output;
    # a later compile start with nothing printed yet (or a clean one) wins.
    last_start, last_lines = segments[-1]
    rep.compile_line = last_start
    seen = set()
    for text in last_lines:
        m = MESSAGE_RE.match(text.replace("\\", "/"))
        if m:
            key = (m["path"], m["line"], m["col"], m["code"], m["msg"])
            if key not in seen:
                seen.add(key)
                rep.messages.append(Message(m["path"], int(m["line"]), int(m["col"]),
                                            m["sev"], m["code"], m["msg"][:MAX_MSG]))
        elif (m := BARE_ERROR_RE.match(text)) and text not in seen:
            seen.add(text)
            rep.bare_errors.append(f"{m['code']} {m['msg'][:MAX_MSG]}")
    rep.earlier_errors = len({m.group(0) for _, seg in segments[:-1] for t in seg
                              if (m := MESSAGE_RE.match(t.replace("\\", "/"))) and m["sev"] == "error"})
    return rep


def _same_dir(a: str, b: Path) -> bool:
    try:
        return Path(a).resolve() == b.resolve() or os.path.samefile(a, b)
    except OSError:
        return os.path.normcase(os.path.normpath(a)) == os.path.normcase(str(b))


def find_log(project: Path, explicit: str | None) -> tuple[Path | None, list[str]]:
    """(log to read, notes). An explicit path wins; otherwise the newest
    candidate that belongs to this project."""
    if explicit:
        path = Path(explicit).expanduser()
        return (path if path.is_file() else None), ([] if path.is_file() else [f"no log at {explicit}"])
    found = [p for p in default_logs(project) if p.is_file()]
    if not found:
        return None, ["no Editor.log found (" + ", ".join(map(_short, default_logs(project)))
                      + "); pass --log PATH (batchmode: the -logFile you used)"]
    owners = {p: parse_project(p) for p in found}
    # This project's logs first, then logs that do not say, then any.
    for pick in ([p for p, o in owners.items() if o and _same_dir(o, project)],
                 [p for p, o in owners.items() if not o], found):
        if pick:
            return max(pick, key=lambda p: p.stat().st_mtime), []
    return None, []


def parse_project(path: Path) -> str:
    try:
        with path.open(encoding="utf-8", errors="replace") as f:
            head = [next(f, "") for _ in range(400)]
    except OSError:
        return ""
    for n, text in enumerate(head):
        if text.strip().lower() == "-projectpath" and n + 1 < len(head):
            return head[n + 1].strip()
    for text in head:
        if m := PROJECT_RE.match(text):
            return m.group(1).strip()
    return ""


def _short(path: Path) -> str:
    home = str(Path.home())
    s = str(path)
    return "~" + s[len(home):] if s.startswith(home) else s


def _when(ts: float) -> str:
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(ts))


def display_path(log_path: str, project: Path, root: Path) -> str:
    """A path from the log (project-relative or absolute) as lotsman prints
    paths: relative to the indexed root, `../` above it."""
    p = Path(log_path)
    full = p if p.is_absolute() else project / p
    return csdefines._display(full, root)


def render_errors(store, root: Path, explicit_log: str | None = None) -> str:
    project = csdefines.project_dir(root)
    log, notes = find_log(project, explicit_log)
    if log is None:
        return "(" + "; ".join(notes) + ")"
    rep = parse(log)
    log_mtime = log.stat().st_mtime
    head = (f"log: {_short(log)} (Unity {rep.version or '?'}, written {_when(log_mtime)}; "
            f"last compilation starts at log line {rep.compile_line or '?'})")
    if rep.project and not _same_dir(rep.project, project):
        return (f"{head}\nthis log belongs to another project ({rep.project}) — its errors "
                "say nothing about this one; open the project in the Editor or pass --log")
    errors = [m for m in rep.messages if m.severity == "error"]
    warnings = [m for m in rep.messages if m.severity == "warning"]
    known = store.known_files()
    edited = sorted(((p, info[1]) for p, info in known.items()
                     if p.endswith((".cs", ".asmdef", ".asmref")) and info[1] > log_mtime),
                    key=lambda x: -x[1])
    edited_paths = {p for p, _ in edited}
    out = [head]
    total = len(errors) + len(rep.bare_errors)
    plural = lambda n, w: f"{n} {w}" + ("" if n == 1 else "s")  # noqa: E731
    out.append(f"{plural(total, 'error')}, {plural(len(warnings), 'warning')} "
               "in the last compilation" + (":" if total else ""))
    for m in errors[:MAX_SHOWN]:
        where = display_path(m.path, project, root)
        mark = "  [file edited after the log]" if where in edited_paths else ""
        out.append(f"  {where}:{m.line}:{m.col} {m.code} {m.text}{mark}")
    if len(errors) > MAX_SHOWN:
        out.append(f"  … {len(errors) - MAX_SHOWN} more")
    out += [f"  {b}" for b in rep.bare_errors[:5]]
    if warnings:
        codes: dict[str, int] = {}
        for w in warnings:
            codes[w.code] = codes.get(w.code, 0) + 1
        out.append("warnings (not listed): " + ", ".join(
            f"{c} ×{k}" for c, k in sorted(codes.items(), key=lambda x: -x[1])[:6]))
    if not total and rep.earlier_errors:
        out.append(f"({rep.earlier_errors} errors in earlier compilations of this log — "
                   "fixed or not recompiled since)")
    if edited:
        # Files with errors first: an edit there is the likeliest fix.
        with_errors = {display_path(m.path, project, root) for m in errors}
        edited.sort(key=lambda x: (x[0] not in with_errors, -x[1]))
        newest = ", ".join(f"{p} ({_when(t)})" for p, t in edited[:MAX_STALE_SHOWN])
        more = f" +{len(edited) - MAX_STALE_SHOWN} more" if len(edited) > MAX_STALE_SHOWN else ""
        what = ("these errors may predate those edits" if total
                else "a clean result may predate those edits")
        out.append(f"stale: {plural(len(edited), 'source file')} changed after the log was "
                   f"written — {what}; recompile (focus the Editor or refresh) before "
                   f"trusting it: {newest}{more}")
    return "\n".join(out)
