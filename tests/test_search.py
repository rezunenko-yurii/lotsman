import unittest

from helpers import FixtureRepoMixin
from lotsman import embed, indexer, search
from lotsman.search import Hit
from lotsman.store import SymbolRow


class TestSearchBasics(FixtureRepoMixin, unittest.TestCase):
    def test_search_finds_symbol(self):
        hits = search.search(self.store, "prepare fuel")
        self.assertTrue(hits)
        self.assertEqual(hits[0].symbol.name, "prepare_fuel")
        self.assertEqual(hits[0].symbol.path, "pkg/core.py")

    def test_search_subtoken_match(self):
        hits = search.search(self.store, "application")
        self.assertTrue(hits)
        self.assertEqual(hits[0].symbol.name, "run_application")


class TestSearchModes(FixtureRepoMixin, unittest.TestCase):
    def _mk_hit(self, path: str, line: int, score: float) -> Hit:
        return Hit(score, SymbolRow(path, "x", "function", line, line, "def x():"))

    def test_rrf_fusion(self):
        # doc B is rank 2 in both lists and must beat docs that are rank 1 in one
        # list but absent from the other.
        list1 = [self._mk_hit("a.py", 1, 9.0), self._mk_hit("b.py", 2, 8.0)]
        list2 = [self._mk_hit("c.py", 3, 0.9), self._mk_hit("b.py", 2, 0.8)]
        fused = search._rrf([list1, list2], limit=3)
        self.assertEqual(fused[0].symbol.path, "b.py")

    def test_bm25_mode_without_vectors(self):
        hits = search.search(self.store, "prepare fuel", mode="bm25")
        self.assertEqual(hits[0].symbol.name, "prepare_fuel")

    def test_auto_mode_degrades_without_vectors(self):
        # Fixture store has no vectors -> auto must behave like bm25, not crash.
        hits = search.search(self.store, "engine", mode="auto")
        self.assertTrue(hits)

    @unittest.skipUnless(embed.available(), "model2vec model not available")
    def test_hybrid_and_vector_modes(self):
        n = embed.embed_missing(self.store)
        self.assertGreater(n, 0)
        self.assertEqual(self.store.vector_count(), n)
        vec_hits = search.search(self.store, "start the motor", mode="vector")
        self.assertTrue(vec_hits)
        hy = search.search(self.store, "prepare fuel", mode="hybrid")
        self.assertEqual(hy[0].symbol.name, "prepare_fuel")
        # re-embed is a no-op (incremental)
        self.assertEqual(embed.embed_missing(self.store), 0)

    @unittest.skipUnless(embed.available(), "model2vec model not available")
    def test_changed_file_reembedded(self):
        embed.embed_missing(self.store)
        (self.tmp / "pkg" / "core.py").write_text("def fresh_symbol():\n    pass\n")
        indexer.index_repo(self.tmp, self.store)
        missing = self.store.conn.execute(
            "SELECT COUNT(*) FROM symbols WHERE vector IS NULL").fetchone()[0]
        self.assertEqual(missing, 1)  # only the rewritten file's symbol
        self.assertEqual(embed.embed_missing(self.store), 1)


if __name__ == "__main__":
    unittest.main()
