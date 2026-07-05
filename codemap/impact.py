"""Impact map: which files changed and who depends on them.

Change detection, in priority order:
1. explicit file list from the caller;
2. `git status --porcelain` when the repo is git-managed;
3. index mtimes within a time window (works for Plastic SCM / no VCS).
"""

from __future__ import annotations

import subprocess
import time
from collections import defaultdict
from pathlib import Path

from codemap.store import Store
from codemap.textutil import estimate_tokens

DEFAULT_SINCE_HOURS = 24.0
DEFAULT_BUDGET = 1500


def detect_changed(root: Path, store: Store,
                   since_hours: float = DEFAULT_SINCE_HOURS) -> tuple[list[str], str]:
    """Returns (changed indexed paths, detection method)."""
    known = store.known_files()
    try:
        out = subprocess.run(
            ["git", "-C", str(root), "status", "--porcelain"],
            capture_output=True, text=True, timeout=15)
    except (OSError, subprocess.TimeoutExpired):
        out = None
    if out is not None and out.returncode == 0:
        paths = []
        for line in out.stdout.splitlines():
            path = line[3:].strip().strip('"')
            if " -> " in path:  # rename: take the new side
                path = path.split(" -> ", 1)[1]
            if path in known:
                paths.append(path)
        return sorted(set(paths)), "git status"

    cutoff = time.time() - since_hours * 3600
    paths = sorted(p for p, (_, mtime, _) in known.items() if mtime >= cutoff)
    return paths, f"mtime window {since_hours:g}h"


def generate_impact(store: Store, changed: list[str],
                    budget: int = DEFAULT_BUDGET) -> str:
    """Changed files with their most-used symbols, then dependent files ranked
    by how much they use names defined in the changed set."""
    changed_set = set(changed)
    if not changed_set:
        return "(no changed files detected)"

    # Names defined in changed files -> symbol rows there.
    defs_in_changed: dict[str, list] = defaultdict(list)
    for path in changed:
        for sym in store.symbols_in_file(path):
            defs_in_changed[sym.name].append(sym)
    names = set(defs_in_changed)

    # Dependents: who references those names from outside the changed set.
    uses_by_name: dict[str, int] = defaultdict(int)      # total external uses
    dependents: dict[str, dict[str, int]] = defaultdict(dict)  # file -> name -> count
    for name in names:
        for path, count in store.files_referencing(name):
            if path in changed_set:
                continue
            dependents[path][name] = count
            uses_by_name[name] += count

    lines: list[str] = [f"Changed files ({len(changed)}):"]
    for path in changed:
        syms = store.symbols_in_file(path)
        lines.append(f"\n{path}:")
        if not syms:
            lines.append("      (no indexed symbols)")
            continue
        ranked_syms = sorted(syms, key=lambda s: uses_by_name.get(s.name, 0),
                             reverse=True)
        for s in ranked_syms[:8]:
            uses = uses_by_name.get(s.name, 0)
            suffix = f"   <- used by others {uses}x" if uses else ""
            lines.append(f"{s.line:5}: {s.signature}{suffix}")

    if dependents:
        lines.append(f"\nImpacted files ({len(dependents)}):")
        ranked_deps = sorted(
            dependents.items(),
            key=lambda kv: sum(kv[1].values()), reverse=True)
        for path, name_counts in ranked_deps:
            top = sorted(name_counts.items(), key=lambda kv: kv[1], reverse=True)
            used = ", ".join(f"{n} ({c}x)" for n, c in top[:4])
            lines.append(f"  {path} — uses {used}")
    else:
        lines.append("\nImpacted files: none (nothing references the changed symbols)")

    # Budget cap: cut whole lines from the tail.
    out: list[str] = []
    used_tokens = 0
    for line in lines:
        cost = estimate_tokens(line + "\n")
        if used_tokens + cost > budget:
            out.append("… (truncated by budget)")
            break
        out.append(line)
        used_tokens += cost
    return "\n".join(out) + "\n"
