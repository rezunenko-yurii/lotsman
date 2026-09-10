"""Unity project facts: assemblies from .asmdef/.asmref (A1)."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from lotsman import indexer, outlineview, unity
from lotsman.changeview import render_change

REPO = Path(__file__).resolve().parent.parent
GUID_LOG = "8d8a2c14c1c942d892a9eddbba19c999"


def _asmdef(path: Path, guid: str | None = None, **fields) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"name": path.stem, **fields}, indent=4))
    if guid:
        Path(str(path) + ".meta").write_text(f"fileFormatVersion: 2\nguid: {guid}\n")


class UnityFixture:
    """A Unity project indexed from its `Assets/` folder (as on SDK-Kitchen)."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="lotsman_unity_"))
        (self.tmp / "ProjectSettings").mkdir()
        a = self.root = self.tmp / "Assets"
        _asmdef(a / "Log/Runtime/ExDebug.asmdef", GUID_LOG)
        (a / "Log/Runtime/ExLog.cs").write_text(
            "public static class ExLog {\n    public static void Info(string m) {}\n}\n")
        _asmdef(a / "Di/ExDi.asmdef", "1" * 32, references=[f"GUID:{GUID_LOG}"])
        (a / "Di/Injector.cs").write_text(
            "class Injector { void Run() { ExLog.Info(\"x\"); } }\n")
        _asmdef(a / "Shop/Core/Shop.Core.asmdef", "2" * 32, references=[],
                noEngineReferences=True)
        (a / "Shop/Core/Processor.cs").write_text(
            "class Processor { void Buy() { ExLog.Info(\"buy\"); } }\n")
        _asmdef(a / "Tools/Editor/Tools.Editor.asmdef", "3" * 32,
                references=["ExDebug", "GUID:" + "f" * 32],
                includePlatforms=["Editor"], autoReferenced=False)
        (a / "Tools/Editor/Menu.cs").write_text("class Menu {}\n")
        (a / "Tools/Editor/Extra").mkdir()
        (a / "Tools/Editor/Extra/Extra.asmref").write_text('{"reference": "Tools.Editor"}')
        (a / "Tools/Editor/Extra/More.cs").write_text("class More {}\n")
        (a / "Game/Editor").mkdir(parents=True)
        (a / "Game/Player.cs").write_text("class Player { void Go() { ExLog.Info(\"p\"); } }\n")
        (a / "Game/Editor/PlayerInspector.cs").write_text("class PlayerInspector {}\n")
        (a / "Plugins").mkdir()
        (a / "Plugins/Native.cs").write_text("class Native {}\n")
        self.store = indexer.open_store(self.root)
        indexer.index_repo(self.root, self.store)
        self.project = unity.load(self.store)

    def tearDown(self):
        self.store.close()
        shutil.rmtree(self.tmp, ignore_errors=True)


class AssemblyModelTests(UnityFixture, unittest.TestCase):
    def asm(self, path: str) -> str:
        return self.project.assembly_for(path)[0].name

    def test_file_to_assembly(self):
        self.assertEqual(self.asm("Log/Runtime/ExLog.cs"), "ExDebug")
        self.assertEqual(self.asm("Tools/Editor/Extra/More.cs"), "Tools.Editor")  # asmref
        self.assertEqual(self.asm("Game/Player.cs"), unity.CSHARP)
        self.assertEqual(self.asm("Game/Editor/PlayerInspector.cs"), unity.EDITOR)
        self.assertEqual(self.asm("Plugins/Native.cs"), unity.FIRSTPASS)

    def test_references_resolve_guid_and_lines(self):
        di = self.project.by_name["ExDi"]
        self.assertEqual(self.project.references(di), [(f"GUID:{GUID_LOG}", 4, "ExDebug")])
        users = {a.name for a, _ in self.project.referenced_by(self.project.by_name["ExDebug"])}
        self.assertEqual(users, {"ExDi", "Tools.Editor"})

    def test_visibility(self):
        p, by = self.project, self.project.by_name
        self.assertTrue(p.can_see(by["ExDi"], by["ExDebug"]))
        self.assertFalse(p.can_see(by["Shop.Core"], by["ExDebug"]))  # references: []
        self.assertTrue(p.can_see(by[unity.CSHARP], by["ExDebug"]))  # autoReferenced
        self.assertFalse(p.can_see(by[unity.CSHARP], by["Tools.Editor"]))  # not auto
        self.assertTrue(p.can_see(by[unity.EDITOR], by[unity.CSHARP]))
        self.assertFalse(p.can_see(by[unity.CSHARP], by[unity.EDITOR]))
        self.assertFalse(p.can_see(by["ExDi"], by[unity.CSHARP]))

    def test_non_unity_repo_has_no_model(self):
        other = Path(tempfile.mkdtemp(prefix="lotsman_plain_"))
        try:
            (other / "a.py").write_text("x = 1\n")
            store = indexer.open_store(other)
            indexer.index_repo(other, store)
            self.assertIsNone(unity.load(store))
            self.assertIn("not a Unity project", unity.render_unity(store, "a.py"))
            store.close()
        finally:
            shutil.rmtree(other, ignore_errors=True)


class UnityViewTests(UnityFixture, unittest.TestCase):
    def test_outline_header_names_the_assembly(self):
        path = "Shop/Core/Processor.cs"
        header = outlineview.outline_header(self.store, path, self.store.symbols_in_file(path))
        self.assertIn("  assembly: Shop.Core — Shop/Core/Shop.Core.asmdef · noEngineReferences",
                      header)

    def test_unity_report_and_verdict(self):
        out = unity.render_unity(self.store, "Shop/Core/Processor.cs", "ExLog")
        self.assertIn("references (Shop.Core.asmdef:3): none", out)
        self.assertIn('ExLog (ExDebug) is NOT visible from Shop.Core: add "ExDebug" '
                      "to references in Shop/Core/Shop.Core.asmdef:3", out)
        out = unity.render_unity(self.store, "ExLog")
        self.assertIn("references (ExDebug.asmdef): none", out)  # no key at all
        self.assertIn("ExDi — Di/ExDi.asmdef:4", out)
        self.assertIn("predefined Assembly-CSharp", out)
        out = unity.render_unity(self.store, "Tools.Editor")
        self.assertIn("Editor only", out)
        self.assertIn("GUID:ffffffff… (not in index)", out)

    def test_change_flags_callers_that_cannot_see_the_owner(self):
        out = render_change(self.store, "ExLog.Info")
        self.assertIn("[Shop.Core cannot see ExDebug]", out)
        self.assertNotIn("[ExDi cannot see", out)
        self.assertNotIn("[Assembly-CSharp cannot see", out)

    def test_cli(self):
        res = subprocess.run(
            [sys.executable, "-m", "lotsman", "--repo", str(self.root), "unity",
             "Game/Player.cs", "ExLog"],
            cwd=REPO, capture_output=True, text=True,
            env={"PYTHONPATH": str(REPO), "LOTSMAN_QUERYLOG": "0", "PATH": "/usr/bin:/bin"})
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertIn("assembly: Assembly-CSharp — (no asmdef)", res.stdout)
        self.assertIn("ExLog (ExDebug) is visible from Assembly-CSharp", res.stdout)


if __name__ == "__main__":
    unittest.main()
