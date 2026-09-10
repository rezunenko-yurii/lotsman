import unittest

from helpers import FixtureRepoMixin
from lotsman import indexer, sliceview


class TestSlice(FixtureRepoMixin, unittest.TestCase):
    def _add(self, rel: str, text: str) -> None:
        (self.tmp / rel).write_text(text)
        indexer.index_repo(self.tmp, self.store)

    def test_target_body_shown_other_bodies_elided(self):
        self._add(
            "pkg/sl.py",
            "def alpha():\n    return 'A-BODY'\n\n\n"
            "def beta():\n    return 'B-BODY'\n",
        )
        out = sliceview.generate_slice(self.store, self.tmp, "pkg/sl.py", "alpha")
        self.assertIn("A-BODY", out)
        self.assertNotIn("B-BODY", out)
        self.assertIn("beta", out)

    def test_method_inside_class_skeleton_keeps_class_line(self):
        self._add(
            "pkg/cls.py",
            "class Engine:\n"
            "    def start(self):\n        return 'START-BODY'\n"
            "    def stop(self):\n        return 'STOP-BODY'\n",
        )
        out = sliceview.generate_slice(self.store, self.tmp, "pkg/cls.py", "start")
        self.assertIn("START-BODY", out)
        self.assertNotIn("STOP-BODY", out)
        self.assertIn("Engine", out)

    def test_skeleton_is_local_not_whole_file(self):
        body = "".join(f"    def m{i:02d}(self):\n        return {i}\n"
                       for i in range(20))
        self._add("pkg/big.py", "class Big:\n" + body)
        out = sliceview.generate_slice(self.store, self.tmp, "pkg/big.py", "m10")
        self.assertIn("class Big", out)          # enclosing type
        for near in ("m07", "m09", "m11", "m13"):
            self.assertIn(near, out)             # neighbors on both sides
        self.assertNotIn("m02", out)
        self.assertNotIn("m18", out)
        self.assertIn("… 7 symbols", out)
        self.assertIn("lists all 21 symbols", out)

    def test_body_keeps_attribute_lines(self):
        (self.tmp / "Suite.cs").write_text(
            "class Suite {\n"
            "    [Test]\n"
            "    [Category(\"slow\")]\n"
            "    public void Runs() { Check(); }\n"
            "}\n")
        indexer.index_repo(self.tmp, self.store)
        out = sliceview.generate_slice(self.store, self.tmp, "Suite.cs", "Runs")
        self.assertIn("    2:     [Test]", out)
        self.assertIn("    4:     public void Runs()", out)

    def test_missing_symbol_and_missing_file(self):
        self._add("pkg/sl2.py", "def gamma():\n    pass\n")
        self.assertIn(
            "no symbol",
            sliceview.generate_slice(self.store, self.tmp, "pkg/sl2.py", "nope"),
        )
        self.assertIn(
            "no symbols indexed",
            sliceview.generate_slice(self.store, self.tmp, "pkg/ghost.py", "x"),
        )


if __name__ == "__main__":
    unittest.main()
