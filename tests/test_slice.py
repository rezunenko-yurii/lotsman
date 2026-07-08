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
