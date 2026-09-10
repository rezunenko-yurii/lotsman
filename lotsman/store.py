"""SQLite-backed index storage with incremental updates."""

from __future__ import annotations

import json
import sqlite3
import time
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
CREATE TABLE IF NOT EXISTS bases (
    file_id INTEGER NOT NULL REFERENCES files(id) ON DELETE CASCADE,
    type_name TEXT NOT NULL,
    base_name TEXT NOT NULL,
    line INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS assemblies (
    file_id INTEGER NOT NULL REFERENCES files(id) ON DELETE CASCADE,
    kind TEXT NOT NULL,
    name TEXT NOT NULL,
    data TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS directives (
    file_id INTEGER NOT NULL REFERENCES files(id) ON DELETE CASCADE,
    kind TEXT NOT NULL,
    line INTEGER NOT NULL,
    end_line INTEGER NOT NULL,
    cond TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sig_changes (
    path TEXT NOT NULL,
    name TEXT NOT NULL,
    old TEXT NOT NULL,
    at REAL NOT NULL,
    PRIMARY KEY (path, name)
);
CREATE TABLE IF NOT EXISTS asset_refs (
    file_id INTEGER NOT NULL REFERENCES files(id) ON DELETE CASCADE,
    kind TEXT NOT NULL,
    key TEXT NOT NULL,
    owner TEXT NOT NULL,
    line INTEGER NOT NULL,
    count INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS guids (
    guid TEXT NOT NULL,
    file_id INTEGER NOT NULL REFERENCES files(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_asset_refs_key ON asset_refs(key);
CREATE INDEX IF NOT EXISTS idx_asset_refs_file ON asset_refs(file_id);
CREATE INDEX IF NOT EXISTS idx_guids_guid ON guids(guid);
CREATE INDEX IF NOT EXISTS idx_guids_file ON guids(file_id);
CREATE INDEX IF NOT EXISTS idx_directives_file ON directives(file_id);
CREATE INDEX IF NOT EXISTS idx_bases_base ON bases(base_name);
CREATE INDEX IF NOT EXISTS idx_bases_type ON bases(type_name);
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
# Markdown heading sections: listed by outline and found by docs search
# (FTS5 over their text), never ranked, code-searched or named by defs/refs.
DOC_KINDS = ("section",)
# JSON config keys: outline only (`outline config.json`).
DATA_KINDS = ("key",)
UNRANKED_KINDS = MEMBER_KINDS + DOC_KINDS + DATA_KINDS
# Files that reference code without being code: their references show in
# refs/change/impact, but never feed the map's ranking graph.
NON_CODE_LANGS = ("markdown", "json", "asmdef", "unityasset", "unitymeta")
# SQL filter on the `symbols s` alias: symbols that rank, embed and code-search.
RANKED_SQL = "s.kind NOT IN (%s)" % ", ".join(f"'{k}'" for k in UNRANKED_KINDS)

# How long a pre-edit method signature is remembered for `impact`'s caller
# check: one working session; afterwards the edit is done.
SIG_CHANGE_TTL = 24 * 3600
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
        if "arity" not in cols:
            self.conn.execute("ALTER TABLE symbols ADD COLUMN arity TEXT")
            self.conn.commit()
        cols = {row[1] for row in self.conn.execute("PRAGMA table_info(idents)")}
        if "lines" not in cols:
            self.conn.execute(
                "ALTER TABLE idents ADD COLUMN lines TEXT NOT NULL DEFAULT ''")
            self.conn.commit()
        # Docs search: one FTS5 row per section, rowid = symbols.id. Terms are
        # pre-normalized (see docs.normalize: Russian stemming), so the default
        # tokenizer only splits on spaces. No FTS5 in this SQLite build ->
        # docs are still outlined, just not searched (doctor reports it).
        try:
            self.conn.execute(
                "CREATE VIRTUAL TABLE IF NOT EXISTS docs_fts USING fts5("
                "heading, body, tokenize = 'unicode61 remove_diacritics 0')")
            self.has_fts = True
        except sqlite3.OperationalError:
            self.has_fts = False

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
        self.conn.execute("DELETE FROM bases")
        self.conn.execute("DELETE FROM assemblies")
        self.conn.execute("DELETE FROM directives")
        self.conn.execute("DELETE FROM sig_changes")
        self.conn.execute("DELETE FROM asset_refs")
        self.conn.execute("DELETE FROM guids")
        if self.has_fts:
            self.conn.execute("DELETE FROM docs_fts")
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
                    ident_lines: dict[str, list[int]] | None = None,
                    doc_text: dict[tuple[str, int], tuple[str, str]] | None = None,
                    bases: list[tuple[str, str, int]] | None = None,
                    assembly: tuple[str, str, str] | None = None,
                    directives: list | None = None,
                    arities: dict[tuple[str, int], str] | None = None,
                    asset_refs: list | None = None,
                    guid: str | None = None,
                    ) -> None:
        """Replace a file's index data atomically (caller batches in a transaction).
        doc_text: (section name, line) -> (heading terms, body terms);
        assembly: (kind, name, JSON data) of a Unity `.asmdef`/`.asmref`;
        directives: C# `#if` branches and `#define`s (csdefines.Directive);
        arities: (name, line) -> encoded parameter shape (callcheck.Arity);
        asset_refs: serialized references of a Unity asset (unityassets.AssetRef);
        guid: the GUID a Unity `.meta` file assigns to the file next to it."""
        cur = self.conn.execute("SELECT id FROM files WHERE path = ?", (path,))
        row = cur.fetchone()
        if row:
            file_id = row[0]
            self.conn.execute(
                "UPDATE files SET sha=?, mtime=?, size=?, lang=? WHERE id=?",
                (sha, mtime, size, lang, file_id))
            self._delete_doc_text([file_id])
            self.conn.execute("DELETE FROM symbols WHERE file_id=?", (file_id,))
            self.conn.execute("DELETE FROM idents WHERE file_id=?", (file_id,))
            self.conn.execute("DELETE FROM bases WHERE file_id=?", (file_id,))
            self.conn.execute("DELETE FROM assemblies WHERE file_id=?", (file_id,))
            self.conn.execute("DELETE FROM directives WHERE file_id=?", (file_id,))
            self.conn.execute("DELETE FROM asset_refs WHERE file_id=?", (file_id,))
            self.conn.execute("DELETE FROM guids WHERE file_id=?", (file_id,))
        else:
            cur = self.conn.execute(
                "INSERT INTO files(path, sha, mtime, size, lang) VALUES(?,?,?,?,?)",
                (path, sha, mtime, size, lang))
            file_id = cur.lastrowid
        arities = arities or {}
        self.conn.executemany(
            "INSERT INTO symbols(file_id, name, kind, line, end_line, signature, arity) "
            "VALUES(?,?,?,?,?,?,?)",
            [(file_id, n, k, l, e, sig, arities.get((n, l))) for n, k, l, e, sig in symbols])
        ident_lines = ident_lines or {}
        self.conn.executemany(
            "INSERT INTO idents(file_id, name, count, lines) VALUES(?,?,?,?)",
            [(file_id, n, c, _encode_lines(ident_lines.get(n)))
             for n, c in idents.items()])
        if bases:
            self.conn.executemany(
                "INSERT INTO bases(file_id, type_name, base_name, line) VALUES(?,?,?,?)",
                [(file_id, t, b, ln) for t, b, ln in bases])
        if directives:
            self.conn.executemany(
                "INSERT INTO directives(file_id, kind, line, end_line, cond) VALUES(?,?,?,?,?)",
                [(file_id, d.kind, d.line, d.end_line, d.cond) for d in directives])
        if assembly:
            self.conn.execute(
                "INSERT INTO assemblies(file_id, kind, name, data) VALUES(?,?,?,?)",
                (file_id, *assembly))
        if asset_refs:
            self.conn.executemany(
                "INSERT INTO asset_refs(file_id, kind, key, owner, line, count) "
                "VALUES(?,?,?,?,?,?)",
                [(file_id, r.kind, r.key, r.owner, r.line, r.count) for r in asset_refs])
        if guid:
            self.conn.execute("INSERT INTO guids(guid, file_id) VALUES(?,?)", (guid, file_id))
        if doc_text and self.has_fts:
            ids = self.conn.execute(
                "SELECT id, name, line FROM symbols WHERE file_id=? AND kind='section'",
                (file_id,)).fetchall()
            self.conn.executemany(
                "INSERT INTO docs_fts(rowid, heading, body) VALUES(?,?,?)",
                [(sid, *doc_text[(name, line)]) for sid, name, line in ids
                 if (name, line) in doc_text])

    def _delete_doc_text(self, file_ids: list[int]) -> None:
        if not self.has_fts or not file_ids:
            return
        ph = ",".join("?" for _ in file_ids)
        self.conn.execute(
            "DELETE FROM docs_fts WHERE rowid IN (SELECT id FROM symbols "
            f"WHERE kind='section' AND file_id IN ({ph}))", file_ids)

    def delete_files(self, paths: list[str]) -> None:
        ph = ",".join("?" for _ in paths)
        ids = [r[0] for r in self.conn.execute(
            f"SELECT id FROM files WHERE path IN ({ph})", paths)] if paths else []
        self._delete_doc_text(ids)
        self.conn.executemany("DELETE FROM files WHERE path = ?", [(p,) for p in paths])
        self.conn.executemany("DELETE FROM sig_changes WHERE path = ?", [(p,) for p in paths])

    def commit(self) -> None:
        self.conn.commit()

    # --- graph inputs ------------------------------------------------------

    def definitions(self) -> dict[str, set[str]]:
        """symbol name -> set of defining file paths."""
        cur = self.conn.execute(
            "SELECT s.name, f.path FROM symbols s JOIN files f ON f.id = s.file_id "
            f"WHERE {RANKED_SQL}")
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
            f"WHERE i.name IN (SELECT DISTINCT name FROM symbols s WHERE {RANKED_SQL}) "
            "AND f.lang NOT IN ('markdown', 'json')")
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
            f"WHERE {RANKED_SQL}")
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
            f"FROM symbols s JOIN files f ON f.id = s.file_id WHERE {RANKED_SQL}")
        return [SymbolRow(*row) for row in cur]

    def symbols_with_vectors(self) -> list[tuple[SymbolRow, bytes]]:
        cur = self.conn.execute(
            "SELECT f.path, s.name, s.kind, s.line, s.end_line, s.signature, s.vector "
            "FROM symbols s JOIN files f ON f.id = s.file_id "
            f"WHERE s.vector IS NOT NULL AND {RANKED_SQL}")
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
            "WHERE s.name = ? AND s.kind NOT IN ('section', 'key') "
            "ORDER BY f.path, s.line", (name,))
        return [SymbolRow(*row) for row in cur]

    def search_docs(self, match: str, limit: int) -> list[tuple]:
        """(path, line, own_end, signature, score) for an FTS5 MATCH
        expression; best first. Headings weigh 4x the section text. own_end
        stops at the next heading of any level: the text that matched, not
        the subsections (a `# Findings` hit must not mean reading the file)."""
        if not self.has_fts or not match:
            return []
        try:
            return self.conn.execute(
                "SELECT f.path, s.line, COALESCE((SELECT MIN(n.line) - 1 FROM symbols n "
                "WHERE n.file_id = s.file_id AND n.kind = 'section' AND n.line > s.line), "
                "s.end_line), s.signature, bm25(docs_fts, 4.0, 1.0) AS score "
                "FROM docs_fts d JOIN symbols s ON s.id = d.rowid "
                "JOIN files f ON f.id = s.file_id "
                "WHERE docs_fts MATCH ? ORDER BY score LIMIT ?",
                (match, limit)).fetchall()
        except sqlite3.OperationalError:
            return []

    def subtypes(self, names: set[str]) -> list[tuple[str, str, str, int]]:
        """(type, direct supertype, path, line) for types deriving from names."""
        if not names:
            return []
        ph = ",".join("?" for _ in names)
        return self.conn.execute(
            "SELECT b.type_name, b.base_name, f.path, b.line FROM bases b "
            f"JOIN files f ON f.id = b.file_id WHERE b.base_name IN ({ph}) "
            "ORDER BY f.path, b.line", list(names)).fetchall()

    def supertypes(self, names: set[str]) -> list[tuple[str, str]]:
        """(type, direct supertype) for the given type names."""
        if not names:
            return []
        ph = ",".join("?" for _ in names)
        return self.conn.execute(
            f"SELECT type_name, base_name FROM bases WHERE type_name IN ({ph})",
            list(names)).fetchall()

    def directives_in_file(self, path: str) -> list:
        """C# `#if` branches and `#define`s of one file, by line."""
        from lotsman.csdefines import Directive
        return [Directive(*row) for row in self.conn.execute(
            "SELECT d.kind, d.line, d.end_line, d.cond FROM directives d "
            "JOIN files f ON f.id = d.file_id WHERE f.path = ? ORDER BY d.line", (path,))]

    def method_arities(self, path: str) -> list[tuple[str, int, str | None, str]]:
        """(name, line, encoded arity, signature) of one file's methods."""
        return self.conn.execute(
            "SELECT s.name, s.line, s.arity, s.signature FROM symbols s "
            "JOIN files f ON f.id = s.file_id WHERE f.path = ? AND s.kind = 'method' "
            "ORDER BY s.line", (path,)).fetchall()

    def arity_at(self, path: str, line: int) -> str | None:
        row = self.conn.execute(
            "SELECT s.arity FROM symbols s JOIN files f ON f.id = s.file_id "
            "WHERE f.path = ? AND s.line = ? AND s.arity IS NOT NULL", (path, line)).fetchone()
        return row[0] if row else None

    def note_sig_change(self, path: str, name: str, old: list[str], new: list[str],
                        at: float) -> None:
        """Remember a method's parameter shapes before an edit. The first
        seen version stays the baseline across later edits; returning to it
        forgets the change. A baseline older than SIG_CHANGE_TTL no longer
        counts: the edit it tracked is done."""
        self.conn.execute("DELETE FROM sig_changes WHERE path=? AND name=? AND at < ?",
                          (path, name, at - SIG_CHANGE_TTL))
        row = self.conn.execute("SELECT old FROM sig_changes WHERE path=? AND name=?",
                                (path, name)).fetchone()
        base = json.loads(row[0]) if row else old
        if sorted(base) == sorted(new):
            self.conn.execute("DELETE FROM sig_changes WHERE path=? AND name=?", (path, name))
        elif not row:
            self.conn.execute("INSERT INTO sig_changes(path, name, old, at) VALUES(?,?,?,?)",
                              (path, name, json.dumps(sorted(old)), at))

    def sig_changes(self, path: str, now: float | None = None) -> list[tuple[str, str]]:
        """(method name, old shapes JSON) remembered for a file within
        SIG_CHANGE_TTL."""
        since = (time.time() if now is None else now) - SIG_CHANGE_TTL
        return self.conn.execute("SELECT name, old FROM sig_changes WHERE path=? AND at >= ? "
                                 "ORDER BY name", (path, since)).fetchall()

    # --- Unity serialized assets (unityassets) ----------------------------

    def asset_rows(self, kinds: tuple[str, ...], keys: list[str]
                   ) -> list[tuple[str, str, str, str, int, int]]:
        """(asset path, kind, key, owner, line, count) for the given keys."""
        if not keys:
            return []
        out = []
        for i in range(0, len(keys), 500):
            chunk = keys[i:i + 500]
            out += self.conn.execute(
                "SELECT f.path, a.kind, a.key, a.owner, a.line, a.count FROM asset_refs a "
                "JOIN files f ON f.id = a.file_id "
                f"WHERE a.key IN ({','.join('?' * len(chunk))}) "
                f"AND a.kind IN ({','.join('?' * len(kinds))}) ORDER BY f.path, a.line",
                (*chunk, *kinds)).fetchall()
        return out

    def asset_rows_in(self, paths: list[str], kind: str) -> list[tuple[str, str, str]]:
        """(asset path, key, owner) of one kind in the given files."""
        out = []
        for i in range(0, len(paths), 500):
            chunk = paths[i:i + 500]
            out += self.conn.execute(
                "SELECT f.path, a.key, a.owner FROM asset_refs a JOIN files f ON f.id = a.file_id "
                f"WHERE f.path IN ({','.join('?' * len(chunk))}) AND a.kind = ?",
                (*chunk, kind)).fetchall()
        return out

    def guid_of(self, path: str) -> str | None:
        """GUID from the `.meta` next to `path`, if indexed."""
        row = self.conn.execute(
            "SELECT g.guid FROM guids g JOIN files f ON f.id = g.file_id WHERE f.path = ?",
            (path + ".meta",)).fetchone()
        return row[0] if row else None

    def guid_paths(self, guids: list[str]) -> dict[str, str]:
        """GUID -> path of the file its `.meta` belongs to."""
        out: dict[str, str] = {}
        for i in range(0, len(guids), 500):
            chunk = guids[i:i + 500]
            for guid, meta in self.conn.execute(
                    "SELECT g.guid, f.path FROM guids g JOIN files f ON f.id = g.file_id "
                    f"WHERE g.guid IN ({','.join('?' * len(chunk))})", chunk):
                out[guid] = meta[:-5]
        return out

    def asset_ref_keys(self, kind: str) -> list[tuple[str, str, str, int, int]]:
        """(key, owner, asset path, line, count) of every row of one kind."""
        return self.conn.execute(
            "SELECT a.key, a.owner, f.path, a.line, a.count FROM asset_refs a "
            "JOIN files f ON f.id = a.file_id WHERE a.kind = ? ORDER BY f.path, a.line",
            (kind,)).fetchall()

    def asset_keys(self, kind: str) -> set[str]:
        return {k for (k,) in self.conn.execute(
            "SELECT DISTINCT key FROM asset_refs WHERE kind = ?", (kind,))}

    def meta_guids(self) -> dict[str, str]:
        """path (without `.meta`) -> GUID for every indexed `.meta`."""
        return {path[:-5]: guid for guid, path in self.conn.execute(
            "SELECT g.guid, f.path FROM guids g JOIN files f ON f.id = g.file_id")}

    # --- unused candidates (unused) ----------------------------------------

    def symbols_of_lang(self, lang: str, kinds: tuple[str, ...]) -> list[SymbolRow]:
        cur = self.conn.execute(
            "SELECT f.path, s.name, s.kind, s.line, s.end_line, s.signature "
            "FROM symbols s JOIN files f ON f.id = s.file_id "
            f"WHERE f.lang = ? AND s.kind IN ({','.join('?' * len(kinds))}) "
            "ORDER BY f.path, s.line", (lang, *kinds))
        return [SymbolRow(*row) for row in cur]

    def ident_lines(self, names: list[str]) -> dict[str, list[tuple[str, list[int]]]]:
        """name -> (path, lines) of every file naming it."""
        out: dict[str, list[tuple[str, list[int]]]] = defaultdict(list)
        for i in range(0, len(names), 500):
            chunk = names[i:i + 500]
            for name, path, lines in self.conn.execute(
                    "SELECT i.name, f.path, i.lines FROM idents i "
                    "JOIN files f ON f.id = i.file_id "
                    f"WHERE i.name IN ({','.join('?' * len(chunk))})", chunk):
                out[name].append((path, _decode_lines(lines)))
        return out

    def paths_of_lang(self, lang: str) -> list[str]:
        return [p for (p,) in self.conn.execute(
            "SELECT path FROM files WHERE lang = ? ORDER BY path", (lang,))]

    def all_bases(self) -> list[tuple[str, str]]:
        return self.conn.execute("SELECT DISTINCT type_name, base_name FROM bases").fetchall()

    def paths_named(self, filename: str) -> list[str]:
        return [p for (p,) in self.conn.execute(
            "SELECT path FROM files WHERE path = ? OR path LIKE ?",
            (filename, "%/" + filename))]

    def has_file(self, path: str) -> bool:
        return self.conn.execute("SELECT 1 FROM files WHERE path=?", (path,)).fetchone() is not None

    def assembly_rows(self) -> list[tuple[str, str, str, str]]:
        """(path, kind, name, data) of every indexed `.asmdef`/`.asmref`."""
        return self.conn.execute(
            "SELECT f.path, a.kind, a.name, a.data FROM assemblies a "
            "JOIN files f ON f.id = a.file_id").fetchall()

    def binding_docs(self) -> set[str]:
        return set(json.loads(self.get_meta("binding_docs") or "[]"))

    def doc_paths(self) -> set[str]:
        return {p for (p,) in self.conn.execute(
            "SELECT path FROM files WHERE lang = 'markdown'")}

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
