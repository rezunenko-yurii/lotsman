import shutil
import tempfile
import unittest
from pathlib import Path

from lotsman.scanner import is_ignored


class TestIgnoreFile(unittest.TestCase):
    def test_patterns(self):
        pats = ["Plugins/", "*.gen.cs", "vendor/*.js", "# not a pattern"]
        self.assertTrue(is_ignored("Plugins/Foo/Bar.cs", pats))
        self.assertTrue(is_ignored("src/Models.gen.cs", pats))
        self.assertTrue(is_ignored("vendor/lib.js", pats))
        self.assertFalse(is_ignored("src/Plugins.cs", pats))
        self.assertFalse(is_ignored("ExFramework/Core.cs", pats))

    def test_scan_respects_ignore(self):
        tmp = Path(tempfile.mkdtemp(prefix="lotsman_ign_"))
        try:
            (tmp / "Plugins").mkdir()
            (tmp / "Plugins" / "vendor.py").write_text("def v():\n    pass\n")
            (tmp / "mine.py").write_text("def m():\n    pass\n")
            (tmp / ".lotsmanignore").write_text("# vendored\nPlugins/\n")
            from lotsman import scanner
            paths = {r.path for r in scanner.scan(tmp)}
            self.assertEqual(paths, {"mine.py"})
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_unity_hidden_folders_skipped(self):
        # Unity compiles neither `Samples~/` nor `.hidden/`; neither should we.
        tmp = Path(tempfile.mkdtemp(prefix="lotsman_unity_"))
        try:
            (tmp / "ProjectSettings").mkdir()
            for d in ("Assets/Scripts", "Assets/Samples~/Demo", "Assets/.draft"):
                (tmp / d).mkdir(parents=True)
                (tmp / d / "A.cs").write_text("class A {}\n")
            from lotsman import scanner
            self.assertEqual({r.path for r in scanner.scan(tmp)},
                             {"Assets/Scripts/A.cs"})
            # Indexing just Assets/ still knows it is a Unity project.
            self.assertTrue(scanner.is_unity_project(tmp / "Assets"))
            self.assertFalse(scanner.is_unity_project(tmp / "Assets" / "Scripts"))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
