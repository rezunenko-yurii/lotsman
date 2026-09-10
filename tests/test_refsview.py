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

    def test_lines_grouped_per_file(self):
        out = refsview.render_refs(self.store, "prepare_fuel")
        # import on line 1, calls on 6 and 7 — one row per file, no grep needed
        self.assertIn("  pkg/app.py:1,6,7", out)
        self.assertIn("referenced by 1 files, 3 lines", out)

    def test_lines_capped_per_file(self):
        body = "".join(f"prepare_fuel()\n" for _ in range(10))
        (self.tmp / "pkg" / "busy.py").write_text(
            "from pkg.core import prepare_fuel\n" + body)
        indexer.index_repo(self.tmp, self.store)
        out = refsview.render_refs(self.store, "prepare_fuel")
        self.assertIn(f"  pkg/busy.py:1,2,3,4,5,6 (+5)", out)

    def test_file_limit_reports_hidden_count(self):
        for i in range(4):
            (self.tmp / "pkg" / f"extra{i}.py").write_text(
                "from pkg.core import prepare_fuel\n")
        indexer.index_repo(self.tmp, self.store)
        out = refsview.render_refs(self.store, "prepare_fuel", limit=2)
        self.assertIn("referenced by 5 files", out)
        self.assertIn("… 3 more files", out)

    def test_ambiguous_name_is_flagged(self):
        (self.tmp / "pkg" / "other.py").write_text(
            "def prepare_fuel():\n    return 0\n")
        indexer.index_repo(self.tmp, self.store)
        out = refsview.render_refs(self.store, "prepare_fuel")
        self.assertIn("note: 2 files define `prepare_fuel`", out)
        self.assertIn("qualify as `Type.prepare_fuel`", out)
        single = refsview.render_refs(self.store, "run_application")
        self.assertNotIn("note:", single)

    def test_qualified_narrows_definitions_and_ranks_same_line(self):
        (self.tmp / "pkg" / "core.py").write_text(
            "class Tank:\n"
            "    def drain(self):\n"
            "        return 1\n")
        (self.tmp / "pkg" / "sink.py").write_text(
            "class Sink:\n"
            "    def drain(self):\n"
            "        return 2\n")
        (self.tmp / "pkg" / "user1.py").write_text(
            "from pkg.core import Tank\n"
            "Tank().drain()\n")
        (self.tmp / "pkg" / "user3.py").write_text(
            "from pkg.core import Tank\n"
            "t = Tank()\n"
            "s.drain()\n")
        indexer.index_repo(self.tmp, self.store)
        out = refsview.render_refs(self.store, "Tank.drain")
        self.assertIn("pkg/core.py:2", out)
        self.assertNotIn("pkg/sink.py", out)  # the other `drain`
        self.assertNotIn("note:", out)
        strong, _, weak = out.partition("weaker")
        self.assertIn("pkg/user1.py:2", strong)
        self.assertIn("pkg/user3.py:3", weak)  # both names, never on one line


if __name__ == "__main__":
    unittest.main()
