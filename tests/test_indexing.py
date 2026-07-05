import os
import unittest

from helpers import FixtureRepoMixin
from lotsman import indexer


class TestIndexing(FixtureRepoMixin, unittest.TestCase):
    def test_index_contents(self):
        stats = self.store.stats()
        self.assertEqual(stats["files"], 3)
        names = {s.name for s in self.store.all_symbols()}
        self.assertEqual(
            names, {"Engine", "start_engine", "prepare_fuel", "run_application"})

    def test_incremental_noop(self):
        res = indexer.index_repo(self.tmp, self.store)
        self.assertEqual(res.added + res.updated + res.removed, 0)
        self.assertEqual(res.unchanged, 3)

    def test_incremental_change_and_delete(self):
        (self.tmp / "pkg" / "core.py").write_text("def new_func():\n    pass\n")
        (self.tmp / "main.py").unlink()
        res = indexer.index_repo(self.tmp, self.store)
        self.assertEqual(res.updated, 1)
        self.assertEqual(res.removed, 1)
        names = {s.name for s in self.store.all_symbols()}
        self.assertIn("new_func", names)
        self.assertNotIn("Engine", names)

    def test_verify_catches_same_mtime_same_size_change(self):
        target = self.tmp / "pkg" / "core.py"
        st = target.stat()
        old = target.read_text()
        # Same byte length, different content; restore the original timestamp.
        target.write_text(old.replace("prepare_fuel", "prepare_fuex"))
        os.utime(target, ns=(st.st_atime_ns, st.st_mtime_ns))
        assert target.stat().st_size == st.st_size

        res = indexer.index_repo(self.tmp, self.store)
        self.assertEqual(res.updated, 0)  # fast path is fooled — documented risk
        res = indexer.index_repo(self.tmp, self.store, verify=True)
        self.assertEqual(res.updated, 1)  # --verify catches it
        names = {s.name for s in self.store.all_symbols()}
        self.assertIn("prepare_fuex", names)

    def test_refs_lookup(self):
        refs = dict(self.store.files_referencing("prepare_fuel"))
        self.assertIn("pkg/app.py", refs)
        self.assertEqual(refs["pkg/app.py"], 3)  # import + 2 calls


if __name__ == "__main__":
    unittest.main()
