import unittest

from helpers import FixtureRepoMixin
from lotsman import indexer, repomap, textutil


class TestRepoMap(FixtureRepoMixin, unittest.TestCase):
    def test_repo_map_ranks_core(self):
        out = repomap.generate_map(self.store, budget=512)
        self.assertIn("pkg/core.py:", out)
        self.assertIn("prepare_fuel", out)
        # core.py (most referenced) must be listed before app.py
        self.assertLess(out.index("pkg/core.py"), out.index("pkg/app.py"))

    def test_repo_map_focus_excluded(self):
        out = repomap.generate_map(self.store, budget=512,
                                   focus={"pkg/core.py"})
        self.assertNotIn("pkg/core.py:", out)

    def test_repo_map_budget(self):
        out = repomap.generate_map(self.store, budget=48)
        self.assertLessEqual(textutil.estimate_tokens(out), 64)


class TestRankCache(FixtureRepoMixin, unittest.TestCase):
    def test_cache_populated_and_hit(self):
        self.assertIsNone(self.store.load_rank_cache(self.store.state_stamp()))
        out1 = repomap.generate_map(self.store, budget=512)
        cached = self.store.load_rank_cache(self.store.state_stamp())
        self.assertTrue(cached)
        out2 = repomap.generate_map(self.store, budget=512)  # served from cache
        self.assertEqual(out1, out2)

    def test_cache_invalidated_on_change(self):
        repomap.generate_map(self.store, budget=512)
        stamp_before = self.store.state_stamp()
        (self.tmp / "pkg" / "core.py").write_text(
            "def brand_new_core():\n    pass\n")
        indexer.index_repo(self.tmp, self.store)
        stamp_after = self.store.state_stamp()
        self.assertNotEqual(stamp_before, stamp_after)
        self.assertIsNone(self.store.load_rank_cache(stamp_after))

    def test_personalized_bypasses_cache(self):
        repomap.generate_map(self.store, budget=512)  # warm cache
        out = repomap.generate_map(self.store, budget=512,
                                   mentions={"prepare_fuel"})
        self.assertIn("prepare_fuel", out)
        # cache row content must still be the default ranking (unchanged stamp)
        self.assertTrue(self.store.load_rank_cache(self.store.state_stamp()))


if __name__ == "__main__":
    unittest.main()
