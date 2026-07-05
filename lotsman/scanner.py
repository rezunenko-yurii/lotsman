"""File discovery: enumerate indexable source files with language detection."""

from __future__ import annotations

import hashlib
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

LANG_BY_EXT = {
    ".py": "python",
    ".pyi": "python",
    ".js": "javascript",
    ".jsx": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".ts": "typescript",
    ".tsx": "tsx",
    ".go": "go",
    ".rs": "rust",
    ".java": "java",
    ".c": "c",
    ".h": "c",
    ".cpp": "cpp",
    ".cc": "cpp",
    ".cxx": "cpp",
    ".hpp": "cpp",
    ".cs": "csharp",
    ".rb": "ruby",
    ".php": "php",
    ".kt": "kotlin",
    ".swift": "swift",
    ".scala": "scala",
    ".lua": "lua",
    ".sh": "bash",
    ".bash": "bash",
}

SKIP_DIRS = frozenset({
    ".git", ".hg", ".svn", ".lotsman", "node_modules", "__pycache__",
    ".venv", "venv", ".tox", ".mypy_cache", ".pytest_cache", ".ruff_cache",
    "dist", "build", "target", ".next", ".nuxt", "vendor", ".idea", ".vscode",
    "coverage", ".cache", "eggs", ".eggs",
})

MAX_FILE_SIZE = 1_000_000  # skip generated/bundled monsters

IGNORE_FILE = ".lotsmanignore"


def load_ignore_patterns(root: Path) -> list[str]:
    """gitignore-lite: one glob per line, `#` comments, `dir/` matches the whole
    subtree. Patterns match posix-style paths relative to root."""
    path = root / IGNORE_FILE
    if not path.is_file():
        return []
    patterns = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        patterns.append(line)
    return patterns


def is_ignored(rel_path: str, patterns: list[str]) -> bool:
    import fnmatch
    for pat in patterns:
        if pat.endswith("/"):
            if rel_path.startswith(pat) or fnmatch.fnmatch(rel_path + "/", pat + "*"):
                return True
        elif fnmatch.fnmatch(rel_path, pat):
            return True
        elif fnmatch.fnmatch(rel_path.rsplit("/", 1)[-1], pat):
            return True
    return False


@dataclass
class FileRecord:
    path: str  # relative, posix-style
    lang: str
    size: int
    mtime: float

    def read_bytes(self, root: Path) -> bytes:
        return (root / self.path).read_bytes()


def file_sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()[:16]


def _git_files(root: Path) -> list[str] | None:
    """Use git's own ignore logic when available: tracked + untracked-unignored."""
    try:
        out = subprocess.run(
            ["git", "-C", str(root), "ls-files", "-c", "-o", "--exclude-standard"],
            capture_output=True, text=True, timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if out.returncode != 0:
        return None
    return [line for line in out.stdout.splitlines() if line]


def _walk_files(root: Path) -> list[str]:
    result: list[str] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and not d.startswith(".")]
        rel_dir = os.path.relpath(dirpath, root)
        for name in filenames:
            rel = name if rel_dir == "." else os.path.join(rel_dir, name)
            result.append(rel.replace(os.sep, "/"))
    return result


def scan(root: Path) -> list[FileRecord]:
    """Enumerate source files under root with detected language."""
    names = _git_files(root)
    if names is None:
        names = _walk_files(root)
    ignore = load_ignore_patterns(root)
    records: list[FileRecord] = []
    for rel in names:
        parts = rel.split("/")
        if any(p in SKIP_DIRS for p in parts[:-1]):
            continue
        if ignore and is_ignored(rel, ignore):
            continue
        lang = LANG_BY_EXT.get(Path(rel).suffix.lower())
        if lang is None:
            continue
        full = root / rel
        try:
            st = full.stat()
        except OSError:
            continue
        if st.st_size > MAX_FILE_SIZE or st.st_size == 0:
            continue
        records.append(FileRecord(path=rel, lang=lang, size=st.st_size, mtime=st.st_mtime))
    return records
