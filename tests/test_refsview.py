import unittest

from helpers import FixtureRepoMixin
from lotsman import indexer, refsview


class TestQualifiedRefs(FixtureRepoMixin, unittest.TestCase):
    def test_degenerate_names_return_not_found(self):
        for name in ("", ".", "..."):
            with self.subTest(name=name):
                out = refsview.render_refs(self.store, name)
                self.assertEqual(out, f"(`{name}` not found in index)")

    def test_qualified_intersects_both_names(self):
        (self.tmp / "pkg" / "core.py").write_text(
            "class Tank:\n"
            "    def drain(self):\n"
            "        return 1\n")
        (self.tmp / "pkg" / "user1.py").write_text(
            "from pkg.core import Tank\n"
            "Tank().drain()\n")
        (self.tmp / "pkg" / "user2.py").write_text(
            "def drain_swamp():\n"
            "    drain = 1\n")
        indexer.index_repo(self.tmp, self.store)
        out = refsview.render_refs(self.store, "Tank.drain")
        self.assertIn("pkg/user1.py", out)
        self.assertNotIn("pkg/user2.py", out)
        self.assertIn("no type resolution", out)

    def test_unqualified_unchanged(self):
        out = refsview.render_refs(self.store, "prepare_fuel")
        self.assertIn("defined in:", out)


if __name__ == "__main__":
    unittest.main()
