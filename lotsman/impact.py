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

from lotsman.graph import OWNER_GATED_SUFFIXES
from lotsman.store import TYPE_KINDS, Store, enclosing_types
from lotsman.textutil import estimate_tokens, is_generic_name, is_test_path

DEFAULT_SINCE_HOURS = 24.0
DEFAULT_BUDGET = 1500


def _member_owners(store: Store, changed: list[str],
                   defs_in_changed: dict[str, list]) -> dict[str, set[str]]:
    """Member name -> owning types, for C# members of the changed files.
    Not gated: names also defined as types (referenced directly) and
    extension methods (callers never name their static class)."""
    owners: dict[str, set[str]] = defaultdict(set)
    for path in changed:
        if not path.endswith(OWNER_GATED_SUFFIXES):
            continue
        rows = [(r.name, r.kind, r.line, r.end_line)
                for r in store.symbols_in_file(path)]
        for name, types in enclosing_types(rows, ("method", "field", "event")).items():
            owners[name] |= types
    for name in list(owners):
        rows = defs_in_changed.get(name, [])
        if any(r.kind in TYPE_KINDS or "(this " in r.signature for r in rows):
            del owners[name]
    return owners


def _dependent_lines(deps: dict[str, dict[str, int]]) -> list[str]:
    ranked = sorted(deps.items(), key=lambda kv: sum(kv[1].values()), reverse=True)
    lines = []
    for path, name_counts in ranked:
        top = sorted(name_counts.items(), key=lambda kv: kv[1], reverse=True)
        used = ", ".join(f"{n} ({c}x)" for n, c in top[:4])
        lines.append(f"  {path} — uses {used}")
    return lines


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
                    budget: int = DEFAULT_BUDGET,
                    tests_only: bool = False, root: Path | None = None) -> str:
    """Changed files with their most-used symbols, then dependent files ranked
    by how much they use names defined in the changed set. With `root`, C#
    methods whose parameter count changed get their call sites checked
    first (callcheck) — that section is never cut by the budget."""
    changed_set = set(changed)
    if not changed_set:
        return "(no changed files detected)"

    # Names defined in changed files -> symbol rows there.
    defs_in_changed: dict[str, list] = defaultdict(list)
    for path in changed:
        for sym in store.symbols_in_file(path):
            defs_in_changed[sym.name].append(sym)
    names = set(defs_in_changed)
    owners = _member_owners(store, changed, defs_in_changed)
    mentioning: dict[str, set[str]] = {}  # type name -> files naming it

    def names_owner(path: str, types: set[str]) -> bool:
        for t in types:
            if t not in mentioning:
                mentioning[t] = {p for p, _ in store.files_referencing(t)}
            if path in mentioning[t]:
                return True
        return False

    # Dependents: who references those names from outside the changed set.
    # C# members count as direct uses only where the file also names the
    # owning type; elsewhere they are name-only matches (`Label` in a file
    # using UI Toolkit's Label) — listed apart, never dropped.
    uses_by_name: dict[str, int] = defaultdict(int)      # direct external uses
    weak_by_name: dict[str, int] = defaultdict(int)      # name-only matches
    dependents: dict[str, dict[str, int]] = defaultdict(dict)  # file -> name -> count
    weak_dependents: dict[str, dict[str, int]] = defaultdict(dict)
    for name in names:
        if is_generic_name(name):
            continue  # `Add`, `Update`: every file "uses" them
        types = owners.get(name)
        for path, count in store.files_referencing(name):
            if path in changed_set:
                continue
            if types and not names_owner(path, types):
                weak_dependents[path][name] = count
                weak_by_name[name] += count
            else:
                dependents[path][name] = count
                uses_by_name[name] += count
    weak_dependents = {p: nc for p, nc in weak_dependents.items()
                       if p not in dependents}

    if tests_only:
        dependents = {
            path: name_counts for path, name_counts in dependents.items()
            if is_test_path(path)
        }
        weak_dependents = {
            path: name_counts for path, name_counts in weak_dependents.items()
            if is_test_path(path)
        }

    # The disclaimer travels with the output, not just the docs: agents read
    # command output, not READMEs.
    from lotsman import callcheck
    sig_lines = callcheck.render_signature_changes(store, root, changed) if root else []
    lines: list[str] = [
        "note: heuristic, name-based matching (no type resolution) — may miss "
        "reflection/DI/codegen and type-resolved usages",
        f"Changed files ({len(changed)})" + (" (tests only):" if tests_only else ":"),
    ]
    for path in changed:
        syms = store.symbols_in_file(path)
        lines.append(f"\n{path}:")
        if not syms:
            lines.append("      (no indexed symbols)")
            continue
        ranked_syms = sorted(
            syms, key=lambda s: (uses_by_name.get(s.name, 0),
                                 weak_by_name.get(s.name, 0)), reverse=True)
        for s in ranked_syms[:8]:
            uses, weak = uses_by_name.get(s.name, 0), weak_by_name.get(s.name, 0)
            suffix = f"   <- used by others {uses}x" if uses else ""
            if weak:
                suffix += (" " if uses else "   <- ") + f"(+{weak}x name-only)"
            lines.append(f"{s.line:5}: {s.signature}{suffix}")

    if not tests_only:
        from lotsman import unityassets
        lines += unityassets.impact_lines(store, changed)
    if dependents:
        header = f"\nImpacted files ({len(dependents)})"
        if tests_only:
            header += " (tests only)"
        lines.append(header + ":")
        lines += _dependent_lines(dependents)
    else:
        none_line = "\nImpacted files"
        if tests_only:
            none_line += " (tests only)"
        none_line += ": none (nothing references the changed symbols)"
        lines.append(none_line)
    if weak_dependents:
        lines.append(f"\nName-only matches ({len(weak_dependents)} files: a "
                     "member name matches, its type is never named there — "
                     "same-named API, or reached via var/DI/inheritance):")
        lines += _dependent_lines(weak_dependents)

    # Budget cap: cut whole lines from the tail; the signature check comes
    # first and is not cut (it is what breaks the build).
    out: list[str] = [lines[0], *sig_lines, *([""] if sig_lines else [])]
    lines = lines[1:]
    used_tokens = estimate_tokens("\n".join(out))
    for line in lines:
        cost = estimate_tokens(line + "\n")
        if used_tokens + cost > budget:
            out.append("… (truncated by budget)")
            break
        out.append(line)
        used_tokens += cost
    return "\n".join(out) + "\n"
