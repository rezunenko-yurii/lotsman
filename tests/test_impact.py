import shutil
import tempfile
import unittest
from pathlib import Path

from helpers import FixtureRepoMixin
from lotsman import impact, indexer


class TestImpact(FixtureRepoMixin, unittest.TestCase):
    def test_explicit_files(self):
        out = impact.generate_impact(self.store, ["pkg/core.py"])
        self.assertIn("pkg/core.py:", out)
        # app.py uses Engine/start_engine/prepare_fuel from core.py
        self.assertIn("Impacted files", out)
        self.assertIn("pkg/app.py", out)
        self.assertIn("prepare_fuel", out)
        # main.py does not use core.py symbols directly
        self.assertNotIn("main.py —", out)

    def test_confidence_note_in_output(self):
        # The heuristic disclaimer must travel with the output itself — agents
        # read command output, not the docs.
        out = impact.generate_impact(self.store, ["pkg/core.py"])
        self.assertIn("note: heuristic", out)
        self.assertIn("no type resolution", out)

    def test_mtime_detection_no_git(self):
        changed, method = impact.detect_changed(self.tmp, self.store,
                                                since_hours=1.0)
        self.assertIn("mtime", method)  # fixture dir is not a git repo
        self.assertEqual(set(changed),
                         {"pkg/core.py", "pkg/app.py", "main.py"})

    def test_no_dependents(self):
        out = impact.generate_impact(self.store, ["main.py"])
        self.assertIn("Impacted files: none", out)

    def test_budget_truncation(self):
        out = impact.generate_impact(self.store, ["pkg/core.py"], budget=10)
        self.assertIn("truncated by budget", out)

    def test_impact_tests_only_filters_non_tests(self):
        (self.tmp / "pkg" / "test_core.py").write_text(
            "from pkg.core import prepare_fuel\n\ndef test_fuel():\n    prepare_fuel()\n")
        (self.tmp / "pkg" / "consumer.py").write_text(
            "from pkg.core import prepare_fuel\nprepare_fuel()\n")
        indexer.index_repo(self.tmp, self.store)
        out = impact.generate_impact(self.store, ["pkg/core.py"], tests_only=True)
        self.assertIn("test_core.py", out)
        self.assertNotIn("consumer.py", out)


class TestImpactCSharpMembers(unittest.TestCase):
    """`Label` on one class must not count every UI Toolkit `Label` user as
    impacted — but such files are listed apart, not dropped."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="lotsman_test_"))
        (self.tmp / "Rail.cs").write_text(
            "public sealed class RailItem {\n"
            "    public string Label;\n"
            "}\n"
            "public static class RailExt {\n"
            "    public static int Width(this RailItem item) => 1;\n"
            "}\n")
        (self.tmp / "User.cs").write_text(
            "class User { void Run(RailItem r) { var s = r.Label; } }\n")
        (self.tmp / "Other.cs").write_text(
            "class Other { void Run(Label l) { var s = l.Label; } }\n")
        (self.tmp / "Ext.cs").write_text(
            "class Ext { int Run(object o) => o.Width(); }\n")
        self.store = indexer.open_store(self.tmp)
        indexer.index_repo(self.tmp, self.store)

    def tearDown(self):
        self.store.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_member_uses_split_direct_and_name_only(self):
        out = impact.generate_impact(self.store, ["Rail.cs"])
        direct, _, weak = out.partition("Name-only matches")
        self.assertIn("User.cs — uses", direct)
        self.assertNotIn("Other.cs", direct)
        self.assertIn("Other.cs — uses Label", weak)
        self.assertIn("(+2x name-only)", out)  # type `Label` + `.Label`
        # extension methods: callers never name the static class
        self.assertIn("Ext.cs — uses Width", direct)


if __name__ == "__main__":
    unittest.main()
