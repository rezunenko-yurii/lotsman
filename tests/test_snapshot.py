import contextlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from lotsman import indexer, snapshot
from lotsman.doctor import run_doctor
from lotsman.store import Store

REPO = Path(__file__).resolve().parents[1]

ROWS = {
    "files": "SELECT path, sha, size, lang FROM files",
    "symbols": "SELECT f.path, s.name, s.kind, s.line, s.end_line, s.signature, s.arity, "
               "s.vector FROM symbols s JOIN files f ON f.id = s.file_id",
    "idents": "SELECT f.path, i.name, i.count, i.lines FROM idents i "
              "JOIN files f ON f.id = i.file_id",
    "bases": "SELECT f.path, b.type_name, b.base_name, b.line FROM bases b "
             "JOIN files f ON f.id = b.file_id",
    "directives": "SELECT f.path, d.kind, d.line, d.cond FROM directives d "
                  "JOIN files f ON f.id = d.file_id",
}


def rows(store: Store) -> dict[str, list]:
    return {name: sorted(map(repr, store.conn.execute(q))) for name, q in ROWS.items()}


class SnapshotTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="lotsman_x3_"))
        self.root = self.tmp / "repo"
        (self.root / "src").mkdir(parents=True)
        (self.root / "src" / "Engine.cs").write_text(
            "public class Engine : Machine {\n"
            "#if UNITY_EDITOR\n"
            "    public void Start(int speed) { Fuel.Burn(); }\n"
            "#endif\n"
            "}\n")
        (self.root / "src" / "Fuel.cs").write_text(
            "public static class Fuel { public static void Burn() {} }\n")
        (self.root / "src" / "Old.cs").write_text("public class Old {}\n")
        (self.root / "README.md").write_text("# Engine\n\nStarts the engine.\n")
        self.env = mock.patch.dict(os.environ, {
            indexer.INDEX_ENV: str(self.tmp / "a" / "index.db"),
            snapshot.SNAPSHOT_ENV: str(self.tmp / "x.snapshot")})
        self.env.start()
        store = indexer.open_store(self.root)
        indexer.index_repo(self.root, store)
        # a vector, as if embedded (the model may be absent in tests)
        store.conn.execute("UPDATE symbols SET vector = x'00ff' WHERE name = 'Fuel'")
        store.conn.execute("INSERT INTO sig_changes VALUES ('src/Fuel.cs', 'Burn', '0', 1.0)")
        self.info = indexer.export_snapshot(store, self.tmp / "x.snapshot")
        store.close()

    def tearDown(self):
        self.env.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def use_index(self, name: str) -> Path:
        db = self.tmp / name / "index.db"
        os.environ[indexer.INDEX_ENV] = str(db)
        return db

    def cold(self) -> Store:
        store = Store(self.tmp / "cold" / "index.db")
        indexer.index_repo(self.root, store)
        store.conn.execute("UPDATE symbols SET vector = x'00ff' WHERE name = 'Fuel'")
        return store

    def test_export_header_and_session_state(self):
        header = snapshot.read_header(self.tmp / "x.snapshot")
        self.assertEqual((header["format"], header["index_version"], header["files"]),
                         (snapshot.FORMAT, indexer.INDEX_VERSION, 4))
        self.assertEqual(self.info["files"], 4)
        self.assertLess(self.info["bytes"], self.info["db_bytes"])
        self.use_index("b")
        store = indexer.open_store(self.root)
        self.assertEqual((store.restored.note, store.restored.files), ("", 4))
        self.assertEqual(store.conn.execute("SELECT COUNT(*) FROM sig_changes").fetchone(), (0,))
        self.assertEqual({m for (m,) in store.conn.execute("SELECT mtime FROM files")},
                         {snapshot.RESTORED_MTIME})
        store.close()

    def test_restore_then_refresh_equals_a_cold_index(self):
        # the checkout moved on after the snapshot: edit, add, delete, touch
        (self.root / "src" / "Engine.cs").write_text(
            "public class Engine : Machine {\n"
            "    public void Start(int speed, bool eco) { Fuel.Burn(); Tank.Fill(); }\n"
            "}\n")
        (self.root / "src" / "Tank.cs").write_text("public class Tank { public void Fill() {} }\n")
        (self.root / "src" / "Old.cs").unlink()
        os.utime(self.root / "src" / "Fuel.cs", (1, 1))
        self.use_index("b")
        store = indexer.open_store(self.root)
        res = indexer.index_repo(self.root, store)
        self.assertEqual((res.added, res.updated, res.removed, res.unchanged), (1, 1, 1, 2))
        cold = self.cold()
        self.assertEqual(rows(store), rows(cold))
        # Engine.Start changed shape between snapshot and checkout, not here
        self.assertEqual(store.conn.execute("SELECT COUNT(*) FROM sig_changes").fetchone(), (0,))
        # the next refresh takes the mtime fast path again
        self.assertEqual(indexer.index_repo(self.root, store).unchanged, 4)
        store.close()
        cold.close()

    def test_unusable_snapshots_build_instead(self):
        path = self.tmp / "x.snapshot"
        data = path.read_bytes()
        header, body = data.split(b"\n", 1)
        cases = {
            "format 0,": header.replace(f'"{indexer.INDEX_VERSION}"'.encode(), b'"0"')
            + b"\n" + body,
            "truncated": data[:len(data) // 2],
            "not a lotsman snapshot": b"hello\n",
        }
        for i, (want, blob) in enumerate(cases.items()):
            with self.subTest(want):
                path.write_bytes(blob)
                db = self.use_index(f"bad{i}")
                store = indexer.open_store(self.root)
                self.assertIsNotNone(store.restored)
                note = store.restored.note
                self.assertTrue(note)
                if want == "format 0,":
                    self.assertIn("index format 0", note)
                self.assertEqual(indexer.index_repo(self.root, store).added, 4)
                store.close()
                self.assertEqual(sorted(p.name for p in db.parent.iterdir()
                                        if p.name.startswith(".lotsman-")), [])

    def test_never_replaces_an_existing_index(self):
        db = self.use_index("b")
        db.parent.mkdir(parents=True)
        db.write_bytes(b"")
        tmp = db.parent / "t.db"
        tmp.write_bytes(b"x")
        self.assertFalse(snapshot._create(tmp, db))
        self.assertEqual(db.read_bytes(), b"")
        store = indexer.open_store(self.root)  # an existing index: no restore
        self.assertIsNone(store.restored)
        store.close()

    def test_other_embedding_model_drops_vectors(self):
        self.use_index("b")
        with mock.patch.dict(os.environ, {"LOTSMAN_EMBED_MODEL": "other/model"}):
            store = indexer.open_store(self.root)
        self.assertEqual(store.conn.execute(
            "SELECT COUNT(*) FROM symbols WHERE vector IS NOT NULL").fetchone(), (0,))
        store.close()
        self.use_index("c")
        store = indexer.open_store(self.root)
        self.assertEqual(store.conn.execute(
            "SELECT COUNT(*) FROM symbols WHERE vector IS NOT NULL").fetchone(), (1,))
        store.close()

    def test_doctor_reports_the_snapshot(self):
        self.use_index("b")

        def doctor():
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                run_doctor(self.root, as_json=True)
            return {c["name"]: c for c in json.loads(buf.getvalue())["checks"]}

        checks = doctor()
        self.assertIn("restore it from the snapshot", checks["index"]["details"][0])
        self.assertEqual((checks["snapshot"]["status"], checks["snapshot"]["files"]), ("ok", 4))
        with mock.patch.object(indexer, "INDEX_VERSION", "999"):
            self.assertEqual(doctor()["snapshot"]["status"], "warn")
        del os.environ[snapshot.SNAPSHOT_ENV]
        self.assertFalse(doctor()["snapshot"]["exists"])


class SnapshotCliTests(unittest.TestCase):
    def test_export_to_the_default_path_and_restore(self):
        tmp = Path(tempfile.mkdtemp(prefix="lotsman_x3_cli_"))
        self.addCleanup(shutil.rmtree, tmp, True)
        (tmp / "core.py").write_text("class Engine:\n    pass\n")
        (tmp / "app.py").write_text("from core import Engine\nEngine()\n")
        env = {k: v for k, v in os.environ.items() if k != snapshot.SNAPSHOT_ENV}
        env.update({"LOTSMAN_INDEX": str(tmp / "idx" / "index.db"), "LOTSMAN_QUERYLOG": "0",
                    "PYTHONPATH": str(REPO)})

        def run(*args):
            return subprocess.run([sys.executable, "-m", "lotsman", "--repo", str(tmp), *args],
                                  capture_output=True, text=True, env=env, cwd=REPO)

        out = run("index", "--no-embed", "--export", "--json")
        self.assertEqual(out.returncode, 0, out.stderr)
        snap = json.loads(out.stdout)["snapshot"]
        self.assertEqual((Path(snap["path"]), snap["files"]),
                         (tmp.resolve() / snapshot.DEFAULT_REL, 2))
        shutil.rmtree(tmp / "idx")
        out = run("refs", "Engine")
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertIn("index restored from", out.stderr)
        self.assertNotIn("first-time index", out.stderr)
        self.assertIn("app.py", out.stdout)


if __name__ == "__main__":
    unittest.main()
