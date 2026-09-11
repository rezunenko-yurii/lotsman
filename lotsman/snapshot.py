"""Index snapshots: a compressed copy of the index for the repo or a CI
cache. A missing index restores from one, then refreshes incrementally.

Format: one header line (`LOTSMAN-SNAPSHOT {json}`), then the SQLite file
as a zlib stream (lzma packs 13% smaller but takes 8x longer to write).
File mtimes are zeroed, so the first refresh hashes every file: a snapshot
older than the checkout re-extracts what changed and never serves stale
rows (and those re-extractions are not taken for signature edits). Session
state (`sig_changes`) is dropped; the rank cache is kept (its stamp is the
content, so it stays valid while nothing changed).
"""

from __future__ import annotations

import json
import os
import sqlite3
import tempfile
import time
import zlib
from dataclasses import dataclass
from pathlib import Path

from lotsman import __version__, embed

MAGIC = b"LOTSMAN-SNAPSHOT "
FORMAT = 1
SNAPSHOT_ENV = "LOTSMAN_SNAPSHOT"
DEFAULT_REL = ".lotsman.snapshot"
MAX_HEADER = 4096
CHUNK = 1 << 20
LEVEL = 6
RESTORED_MTIME = 0.0  # every restored file row: forces one hash per file


@dataclass
class Restored:
    path: Path
    files: int = 0
    seconds: float = 0.0
    note: str = ""  # why the snapshot was not used ("" when it was)


def default_path(root: Path) -> Path:
    return root / DEFAULT_REL


def find(root: Path) -> Path | None:
    """The snapshot a missing index restores from: `$LOTSMAN_SNAPSHOT`,
    else `.lotsman.snapshot` in the root."""
    env = os.environ.get(SNAPSHOT_ENV)
    path = Path(env).expanduser() if env else default_path(root)
    return path if path.is_file() else None


def _embed_model() -> str:
    return os.environ.get("LOTSMAN_EMBED_MODEL", embed.DEFAULT_MODEL)


def read_header(path: Path) -> dict | None:
    try:
        with path.open("rb") as f:
            line = f.readline(MAX_HEADER)
    except OSError:
        return None
    if not line.startswith(MAGIC) or not line.endswith(b"\n"):
        return None
    try:
        header = json.loads(line[len(MAGIC):])
    except ValueError:
        return None
    return header if isinstance(header, dict) else None


def export(conn: sqlite3.Connection, dest: Path, index_version: str) -> dict:
    """Write a snapshot of the index behind `conn` to `dest` (atomically)."""
    t0 = time.monotonic()
    conn.commit()
    dest.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_db = tempfile.mkstemp(prefix=".lotsman-snap-", suffix=".db", dir=dest.parent)
    os.close(fd)
    tmp_out = Path(tmp_db).with_suffix(".tmp")
    try:
        copy = sqlite3.connect(tmp_db)
        try:
            conn.backup(copy)
            copy.execute("PRAGMA journal_mode = DELETE")
            copy.execute("DELETE FROM sig_changes")
            copy.execute("UPDATE files SET mtime = ?", (RESTORED_MTIME,))
            (files,) = copy.execute("SELECT COUNT(*) FROM files").fetchone()
            copy.commit()
            copy.execute("VACUUM")
        finally:
            copy.close()
        raw = Path(tmp_db).stat().st_size
        header = {"format": FORMAT, "index_version": index_version,
                  "embed_model": _embed_model(), "files": files,
                  "db_bytes": raw, "lotsman": __version__,
                  "created": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
        packer = zlib.compressobj(LEVEL)
        with open(tmp_db, "rb") as src, tmp_out.open("wb") as out:
            out.write(MAGIC + json.dumps(header, sort_keys=True).encode() + b"\n")
            while chunk := src.read(CHUNK):
                out.write(packer.compress(chunk))
            out.write(packer.flush())
        os.replace(tmp_out, dest)
    finally:
        for p in (Path(tmp_db), tmp_out):
            p.unlink(missing_ok=True)
    return {"path": str(dest), "files": files, "db_bytes": raw,
            "bytes": dest.stat().st_size, "seconds": time.monotonic() - t0}


def _create(tmp: Path, db: Path) -> bool:
    """Move `tmp` to `db` only if `db` does not exist: never swap the file
    under a process that already has the index open."""
    try:
        os.link(tmp, db)
        return True
    except FileExistsError:
        return False
    except OSError:  # no hard links on this filesystem
        if db.exists():
            return False
        os.replace(tmp, db)
        return True


def restore(root: Path, db: Path, index_version: str) -> Restored | None:
    """Create the missing index `db` from the snapshot `find` picks. None
    when there is no snapshot; `note` says why one was not used. Never
    replaces an existing index (a concurrent restore or index run wins)."""
    path = find(root)
    if path is None:
        return None
    t0 = time.monotonic()
    res = Restored(path)
    header = read_header(path)
    if header is None or header.get("format") != FORMAT:
        res.note = f"{path} is not a lotsman snapshot this version reads"
        return res
    if header.get("index_version") != index_version:
        res.note = (f"{path} holds index format {header.get('index_version')}, "
                    f"this lotsman needs {index_version} — re-export it")
        return res
    db.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".lotsman-restore-", suffix=".db", dir=db.parent)
    try:
        unpacker = zlib.decompressobj()
        with path.open("rb") as src, os.fdopen(fd, "wb") as out:
            src.readline(MAX_HEADER)
            while chunk := src.read(CHUNK):
                out.write(unpacker.decompress(chunk))
            out.write(unpacker.flush())
        if not unpacker.eof:
            raise zlib.error("truncated")
        conn = sqlite3.connect(tmp)
        try:
            row = conn.execute("SELECT value FROM meta WHERE key='index_version'").fetchone()
            if not row or row[0] != index_version:
                raise sqlite3.DatabaseError("index version inside differs from the header")
            (res.files,) = conn.execute("SELECT COUNT(*) FROM files").fetchone()
            if header.get("embed_model") != _embed_model():
                # Vectors of another model live in another space: re-embed.
                conn.execute("UPDATE symbols SET vector = NULL")
                conn.commit()
        finally:
            conn.close()
        if not _create(Path(tmp), db):
            res.note = "an index appeared meanwhile — kept it"
    except (OSError, zlib.error, sqlite3.DatabaseError) as e:
        res.note = f"{path} is unreadable ({e})"
    finally:
        Path(tmp).unlink(missing_ok=True)
    res.seconds = time.monotonic() - t0
    return res
