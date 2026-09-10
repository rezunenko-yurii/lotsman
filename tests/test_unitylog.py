"""Compile errors from the Unity Editor log (U0)."""

from __future__ import annotations

import os
import time
import unittest

from lotsman import unity, unitylog
from tests.test_unity import UnityFixture


def _log(project: str, body: str, version: str = "2022.3.20f1") -> str:
    # Shape of a real Editor.log header: the command line carries secrets.
    return (f"Unity Editor version:    {version} (abc)\nCOMMAND LINE ARGUMENTS:\n"
            f"/Applications/Unity\n-projectpath\n{project}\n-accessToken\nSECRET-TOKEN\n{body}")


# Unity 2022 wording; the first compilation fails, the second one is the one
# that counts (one error fixed, one left, printed twice).
BODY_2022 = """\
[ScriptCompilation] Requested script compilation because: Assetdatabase observed changes
Assets/Game/Player.cs(3,5): error CS0103: The name 'x' does not exist in the current context
Assets/Di/Injector.cs(1,1): error CS0246: The type 'Foo' could not be found
[ScriptCompilation] Requested script compilation because: Assetdatabase observed changes
Starting: /Unity/netcorerun "ScriptCompilationBuildProgram.exe"
Assets\\Di\\Injector.cs(1,1): error CS0246: The type 'Foo' could not be found
Assets\\Di\\Injector.cs(1,1): error CS0246: The type 'Foo' could not be found
Assets/Game/Player.cs(9,2): warning CS0618: 'Old' is obsolete
error CS2001: Source file 'Assets/Gone.cs' could not be found.
*** Tundra build failed (1.20 seconds), 2 items updated
"""


class EditorLogTests(UnityFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.log = self.tmp / "Editor.log"
        self.log.write_text(_log(str(self.tmp), BODY_2022))
        past = time.time() - 3600
        for f in (self.root / "Di/Injector.cs", self.root / "Game/Player.cs"):
            os.utime(f, (past, past))
        from lotsman import indexer
        indexer.index_repo(self.root, self.store)

    def render(self, log=None):
        return unity.render_unity(self.store, "errors", root=self.root, log=str(log or self.log))

    def test_last_compilation_deduped_warnings_apart(self):
        rep = unitylog.parse(self.log)
        self.assertEqual(rep.version, "2022.3.20f1")
        self.assertEqual(rep.compile_line, 11)
        self.assertEqual([(m.path, m.line, m.severity) for m in rep.messages],
                         [("Assets/Di/Injector.cs", 1, "error"),
                          ("Assets/Game/Player.cs", 9, "warning")])
        self.assertEqual(rep.bare_errors, ["CS2001 Source file 'Assets/Gone.cs' could not be found."])
        self.assertEqual(rep.earlier_errors, 2)

    def test_render_fresh_log(self):
        out = self.render()
        self.assertIn("2 errors, 1 warning in the last compilation:", out)
        self.assertIn("  Di/Injector.cs:1:1 CS0246 The type 'Foo' could not be found", out)
        self.assertNotIn("Player.cs:3", out)          # fixed in the last compilation
        self.assertIn("warnings (not listed): CS0618 ×1", out)
        self.assertNotIn("stale:", out)
        self.assertNotIn("SECRET", out)

    def test_edits_after_the_log_are_flagged(self):
        past = time.time() - 7200
        os.utime(self.log, (past, past))
        out = self.render()
        self.assertIn("Di/Injector.cs:1:1 CS0246 The type 'Foo' could not be found"
                      "  [file edited after the log]", out)
        self.assertRegex(out, r"stale: \d+ source files changed after the log was written — "
                              r"these errors may predate those edits; .*: Di/Injector\.cs \(")

    def test_other_project_and_missing_log(self):
        other = self.tmp / "other.log"
        other.write_text(_log("/somewhere/else", BODY_2022, "6000.3.11f1"))
        out = self.render(other)
        self.assertIn("this log belongs to another project (/somewhere/else)", out)
        self.assertNotIn("CS0246", out)
        self.assertIn("no log at", self.render(self.tmp / "nope.log"))

    def test_clean_last_compilation(self):
        self.log.write_text(_log(str(self.tmp), BODY_2022 + "[ScriptCompilation] Requested "
                                 "script compilation because: x\n*** Tundra build success\n"))
        out = self.render()
        self.assertIn("0 errors, 0 warnings in the last compilation", out)
        self.assertIn("(2 errors in earlier compilations of this log", out)


if __name__ == "__main__":
    unittest.main()
