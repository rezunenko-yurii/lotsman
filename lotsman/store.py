"""SQLite-backed index storage with incremental updates."""

from __future__ import annotations

import sqlite3
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT
);
CREATE TABLE IF NOT EXISTS files (
    id INTEGER PRIMARY KEY,
    path TEXT UNIQUE NOT NULL,
    sha TEXT NOT NULL,
    mtime REAL NOT NULL,
    size INTEGER NOT NULL,
    lang TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS symbols (
    id INTEGER PRIMARY KEY,
    file_id INTEGER NOT NULL REFERENCES files(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    kind TEXT NOT NULL,
    line INTEGER NOT NULL,
    end_line INTEGER NOT NULL,
    signature TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS idents (
    file_id INTEGER NOT NULL REFERENCES files(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    count INTEGER NOT NULL,
    lines TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS rank_cache (
    path TEXT NOT NULL,
    ident TEXT NOT NULL,
    rank REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_symbols_name ON symbols(name);
CREATE INDEX IF NOT EXISTS idx_symbols_file ON symbols(file_id);
CREATE INDEX IF NOT EXISTS idx_idents_name ON idents(name);
CREATE INDEX IF NOT EXISTS idx_idents_file ON idents(file_id);
"""


# Member-level kinds: listed by outline/defs/refs, but kept out of ranking and
# search — thousands of `_mixer`/`Version` fields would crowd out the types
# and methods those rank (measured on a Unity corpus: top-10 overlap fell to
# 2/10 on some queries).
MEMBER_KINDS = ("field", "event")
# SQL filter on the `symbols s` alias.
NOT_MEMBER_SQL = "s.kind NOT IN (%s)" % ", ".join(f"'{k}'" for k in MEMBER_KINDS)

MAX_LINES_PER_ROW = 64  # enough for any per-file listing; bounds DB growth


def _encode_lines(lines: list[int] | None) -> str:
    return ",".join(map(str, lines[:MAX_LINES_PER_ROW])) if lines else ""


def _decode_lines(text: str) -> list[int]:
    return [int(x) for x in text.split(",")] if text else []


TYPE_KINDS = ("class", "type")


def enclosing_types(rows: list[tuple[str, str, int, int]],
                    member_kinds: tuple[str, ...]) -> dict[str, set[str]]:
    """Member name -> innermost types enclosing it, from one file's
    (name, kind, line, end_line) rows. Line ranges stand in for scopes: a
    method inside `class Foo {...}` is owned by `Foo`."""
    types = [(name, line, end) for name, kind, line, end in rows
             if kind in TYPE_KINDS]
    owners: dict[str, set[str]] = defaultdict(set)
    for name, kind, line, _ in rows:
        if kind not in member_kinds:
            continue
        enclosing = [t for t in types if t[1] <= line <= t[2]]
        if enclosing:
            owners[name].add(max(enclosing, key=lambda t: t[1])[0])
    return owners


@dataclass
class SymbolRow:
    path: str
    name: str
    kind: str
    line: int
    end_line: int
    signature: str


class Store:
    def __init__(self, db_path: Path):
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self.db_path = db_path
        self.conn = sqlite3.connect(db_path)
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.execute("PRAGMA journal_mode = WAL")
        self.conn.executescript(SCHEMA)
        self._migrate()

    def _migrate(self) -> None:
        cols = {row[1] for row in self.conn.execute("PRAGMA table_info(symbols)")}
        if "vector" not in cols:
            self.conn.execute("ALTER TABLE symbols ADD COLUMN vector BLOB")
            self.conn.commit()
        # Rows written before this column existed get '' — the INDEX_VERSION
        # bump that came with it reindexes them on the next run.
        cols = {row[1] for row in self.conn.execute("PRAGMA table_info(idents)")}
        if "lines" not in cols:
            self.conn.execute(
                "ALTER TABLE idents ADD COLUMN lines TEXT NOT NULL DEFAULT ''")
            self.conn.commit()

    def close(self) -> None:
        self.conn.commit()
        self.conn.close()

    def get_meta(self, key: str) -> str | None:
        row = self.conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return row[0] if row else None

    def set_meta(self, key: str, value: str) -> None:
        self.conn.execute(
            "INSERT INTO meta(key, value) VALUES(?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))

    def wipe(self) -> None:
        """Drop all indexed data (schema kept); used on index-version bumps."""
        self.conn.execute("DELETE FROM idents")
        self.conn.execute("DELETE FROM symbols")
        self.conn.execute("DELETE FROM files")
        self.conn.execute("DELETE FROM rank_cache")
        self.conn.commit()

    # --- rank cache ---------------------------------------------------------

    def state_stamp(self) -> str:
        """Digest of the indexed content state; changes iff any file changes."""
        import hashlib
        h = hashlib.sha256()
        for path, sha in self.conn.execute(
                "SELECT path, sha FROM files ORDER BY path"):
            h.update(path.encode())
            h.update(sha.encode())
        return h.hexdigest()[:16]

    def load_rank_cache(self, stamp: str) -> list[tuple[str, str, float]] | None:
        if self.get_meta("rank_cache_stamp") != stamp:
            return None
        rows = self.conn.execute(
            "SELECT path, ident, rank FROM rank_cache ORDER BY rank DESC").fetchall()
        return rows or None

    def save_rank_cache(self, stamp: str,
                        ranked: list[tuple[str, str, float]]) -> None:
        self.conn.execute("DELETE FROM rank_cache")
        self.conn.executemany(
            "INSERT INTO rank_cache(path, ident, rank) VALUES(?,?,?)", ranked)
        self.set_meta("rank_cache_stamp", stamp)
        self.conn.commit()

    # --- incremental file bookkeeping -------------------------------------

    def known_files(self) -> dict[str, tuple[str, float, int]]:
        """path -> (sha, mtime, size) for change detection."""
        cur = self.conn.execute("SELECT path, sha, mtime, size FROM files")
        return {p: (s, m, z) for p, s, m, z in cur}

    def upsert_file(self, path: str, sha: str, mtime: float, size: int, lang: str,
                    symbols: list[tuple[str, str, int, int, str]],
                    idents: Counter,
                    ident_lines: dict[str, list[int]] | None = None) -> None:
        """Replace a file's index data atomically (caller batches in a transaction)."""
        cur = self.conn.execute("SELECT id FROM files WHERE path = ?", (path,))
        row = cur.fetchone()
        if row:
            file_id = row[0]
            self.conn.execute(
                "UPDATE files SET sha=?, mtime=?, size=?, lang=? WHERE id=?",
                (sha, mtime, size, lang, file_id))
            self.conn.execute("DELETE FROM symbols WHERE file_id=?", (file_id,))
            self.conn.execute("DELETE FROM idents WHERE file_id=?", (file_id,))
        else:
            cur = self.conn.execute(
                "INSERT INTO files(path, sha, mtime, size, lang) VALUES(?,?,?,?,?)",
                (path, sha, mtime, size, lang))
            file_id = cur.lastrowid
        self.conn.executemany(
            "INSERT INTO symbols(file_id, name, kind, line, end_line, signature) "
            "VALUES(?,?,?,?,?,?)",
            [(file_id, n, k, l, e, sig) for n, k, l, e, sig in symbols])
        ident_lines = ident_lines or {}
        self.conn.executemany(
            "INSERT INTO idents(file_id, name, count, lines) VALUES(?,?,?,?)",
            [(file_id, n, c, _encode_lines(ident_lines.get(n)))
             for n, c in idents.items()])

    def delete_files(self, paths: list[str]) -> None:
        self.conn.executemany("DELETE FROM files WHERE path = ?", [(p,) for p in paths])

    def commit(self) -> None:
        self.conn.commit()

    # --- graph inputs ------------------------------------------------------

    def definitions(self) -> dict[str, set[str]]:
        """symbol name -> set of defining file paths."""
        cur = self.conn.execute(
            "SELECT s.name, f.path FROM symbols s JOIN files f ON f.id = s.file_id "
            f"WHERE {NOT_MEMBER_SQL}")
        result: dict[str, set[str]] = defaultdict(set)
        for name, path in cur:
            result[name].add(path)
        return result

    def references(self) -> dict[str, Counter]:
        """file path -> Counter(identifier -> occurrence count).

        Only identifiers that are defined somewhere in the repo: undefined names
        can never form graph edges, and filtering in SQL keeps the Python-side
        dict small on large repos. Generic names are kept (the graph drops them
        from edges, but still sees which types a file mentions).
        """
        cur = self.conn.execute(
            "SELECT f.path, i.name, i.count FROM idents i "
            "JOIN files f ON f.id = i.file_id "
            f"WHERE i.name IN (SELECT DISTINCT name FROM symbols s WHERE {NOT_MEMBER_SQL})")
        result: dict[str, Counter] = defaultdict(Counter)
        for path, name, count in cur:
            result[path][name] = count
        return result

    def member_owners(self, suffixes: tuple[str, ...]) -> dict[tuple[str, str], set[str]]:
        """(method name, path) -> types enclosing its definitions there, for
        files with the given suffixes (see `enclosing_types`)."""
        cur = self.conn.execute(
            "SELECT f.path, s.name, s.kind, s.line, s.end_line "
            "FROM symbols s JOIN files f ON f.id = s.file_id "
            f"WHERE {NOT_MEMBER_SQL}")
        by_file: dict[str, list[tuple[str, str, int, int]]] = defaultdict(list)
        for path, name, kind, line, end in cur:
            if path.endswith(suffixes):
                by_file[path].append((name, kind, line, end))
        owners: dict[tuple[str, str], set[str]] = {}
        for path, rows in by_file.items():
            for name, types in enclosing_types(rows, ("method",)).items():
                owners[(name, path)] = types
        return owners

    # --- lookups -----------------------------------------------------------

    def all_symbols(self) -> list[SymbolRow]:
        """Rankable/searchable symbols (member kinds excluded)."""
        cur = self.conn.execute(
            "SELECT f.path, s.name, s.kind, s.line, s.end_line, s.signature "
            f"FROM symbols s JOIN files f ON f.id = s.file_id WHERE {NOT_MEMBER_SQL}")
        return [SymbolRow(*row) for row in cur]

    def symbols_with_vectors(self) -> list[tuple[SymbolRow, bytes]]:
        cur = self.conn.execute(
            "SELECT f.path, s.name, s.kind, s.line, s.end_line, s.signature, s.vector "
            "FROM symbols s JOIN files f ON f.id = s.file_id "
            f"WHERE s.vector IS NOT NULL AND {NOT_MEMBER_SQL}")
        return [(SymbolRow(*row[:6]), row[6]) for row in cur]

    def vector_count(self) -> int:
        (n,) = self.conn.execute(
            "SELECT COUNT(*) FROM symbols WHERE vector IS NOT NULL").fetchone()
        return n

    def symbols_in_file(self, path: str) -> list[SymbolRow]:
        cur = self.conn.execute(
            "SELECT f.path, s.name, s.kind, s.line, s.end_line, s.signature "
            "FROM symbols s JOIN files f ON f.id = s.file_id "
            "WHERE f.path = ? ORDER BY s.line", (path,))
        return [SymbolRow(*row) for row in cur]

    def symbols_named(self, name: str) -> list[SymbolRow]:
        cur = self.conn.execute(
            "SELECT f.path, s.name, s.kind, s.line, s.end_line, s.signature "
            "FROM symbols s JOIN files f ON f.id = s.file_id "
            "WHERE s.name = ? ORDER BY f.path, s.line", (name,))
        return [SymbolRow(*row) for row in cur]

    def files_referencing(self, name: str) -> list[tuple[str, int]]:
        cur = self.conn.execute(
            "SELECT f.path, i.count FROM idents i JOIN files f ON f.id = i.file_id "
            "WHERE i.name = ? ORDER BY i.count DESC", (name,))
        return list(cur)

    def reference_sites(self, name: str) -> list[tuple[str, int, list[int]]]:
        """(path, count, lines) per referencing file, most uses first."""
        cur = self.conn.execute(
            "SELECT f.path, i.count, i.lines FROM idents i "
            "JOIN files f ON f.id = i.file_id "
            "WHERE i.name = ? ORDER BY i.count DESC, f.path", (name,))
        return [(path, count, _decode_lines(lines)) for path, count, lines in cur]

    def files_referencing_all(self, names: list[str]) -> list[tuple[str, int]]:
        ph = ",".join("?" for _ in names)
        cur = self.conn.execute(
            "SELECT f.path, MIN(i.count) AS score "
            "FROM idents i JOIN files f ON f.id = i.file_id "
            f"WHERE i.name IN ({ph}) GROUP BY f.path "
            "HAVING COUNT(DISTINCT i.name) = ? ORDER BY score DESC",
            (*names, len(names)))
        return [(path, count) for path, count in cur]

    def stats(self) -> dict:
        (files,) = self.conn.execute("SELECT COUNT(*) FROM files").fetchone()
        (symbols,) = self.conn.execute("SELECT COUNT(*) FROM symbols").fetchone()
        (idents,) = self.conn.execute("SELECT COUNT(*) FROM idents").fetchone()
        langs = dict(self.conn.execute(
            "SELECT lang, COUNT(*) FROM files GROUP BY lang ORDER BY 2 DESC"))
        return {"files": files, "symbols": symbols, "ident_rows": idents,
                "languages": langs,
                "db_bytes": self.db_path.stat().st_size if self.db_path.exists() else 0}
