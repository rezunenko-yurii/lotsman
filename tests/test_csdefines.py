"""C# `#if` regions and Unity define symbols (P1)."""

from __future__ import annotations

import unittest

from lotsman import csdefines, outlineview, unity
from lotsman.csdefines import evaluate, scan, version_matches
from tests.test_unity import UnityFixture, _asmdef

SOURCE = """﻿#if UNITY_EDITOR
#define LOCAL
class A {}
#elif UNITY_ANDROID // trailing comment
class B {}
#else
class C {
#if DEBUG
    void D() {}
#endif
}
#endif
#if UNITY_IOS && !UNITY_EDITOR
class N {}
#endif
#if OPEN
class U {}
""".encode()


class ScanTests(unittest.TestCase):
    def test_effective_conditions(self):
        got = [(d.kind, d.line, d.end_line, d.cond) for d in scan(SOURCE)]
        self.assertEqual(got, [
            ("if", 1, 4, "UNITY_EDITOR"),          # BOM before the first #if
            ("define", 2, 2, "LOCAL"),
            ("if", 4, 6, "!UNITY_EDITOR && UNITY_ANDROID"),
            ("if", 6, 12, "!UNITY_EDITOR && !UNITY_ANDROID"),
            ("if", 8, 10, "(!UNITY_EDITOR && !UNITY_ANDROID) && DEBUG"),
            ("if", 13, 15, "UNITY_IOS && !UNITY_EDITOR"),
            ("if", 16, 18, "OPEN"),                # unterminated: runs to EOF
        ])

    def test_region_at_is_innermost(self):
        dirs = scan(SOURCE)
        self.assertEqual(csdefines.region_at(dirs, 9).cond,
                         "(!UNITY_EDITOR && !UNITY_ANDROID) && DEBUG")
        self.assertEqual(csdefines.region_at(dirs, 7).line, 6)
        self.assertIsNone(csdefines.region_at(dirs, 13))  # the directive line itself


class EvaluateTests(unittest.TestCase):
    def test_three_valued(self):
        env = {"A": True, "B": False, "U": None}.get
        self.assertIs(evaluate("A && !B", env), True)
        self.assertIs(evaluate("U && B", env), False)   # false wins over unknown
        self.assertIs(evaluate("U || A", env), True)
        self.assertIsNone(evaluate("U && A", env))
        self.assertIs(evaluate("(A == true) != B", env), True)
        self.assertIsNone(evaluate("A &&", env))         # malformed
        self.assertIsNone(evaluate("A + B", env))

    def test_version_expressions(self):
        self.assertTrue(version_matches("1.2.3", "1.2.0"))
        self.assertFalse(version_matches("1.1.9", "1.2.0"))
        self.assertTrue(version_matches("2.0.0", "[1.0,3.0]"))
        self.assertFalse(version_matches("3.0.0", "[1.0,3.0)"))
        self.assertTrue(version_matches("1.0.0", "[1.0.0]"))
        self.assertTrue(version_matches("0.9.0-preview.2", "(,1.0)"))


class ProjectDefinesTests(UnityFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        ps = self.tmp / "ProjectSettings"
        (ps / "ProjectVersion.txt").write_text("m_EditorVersion: 2022.3.20f1\n")
        (ps / "ProjectSettings.asset").write_text(
            "PlayerSettings:\n  scriptingDefineSymbols:\n    Android: SHOP_ANDROID;ADS\n"
            "    iPhone: ADS\n  additionalCompilerArguments: {}\n")
        (self.root / "csc.rsp").write_text("-define:RSP_ALL\n")
        (self.tmp / "Packages").mkdir()
        (self.tmp / "Packages/manifest.json").write_text(
            '{"dependencies": {"com.unity.purchasing": "4.9.0"}}')
        _asmdef(self.root / "Ads/Ads.asmdef", "4" * 32, includePlatforms=["iOS", "Android"],
                defineConstraints=["ADS"],
                versionDefines=[{"name": "com.unity.purchasing", "expression": "4.0.0",
                                 "define": "HAS_IAP"}])
        (self.root / "Ads/Banner.cs").write_text(
            "#if HAS_IAP && UNITY_ANDROID\nclass Banner {}\n#endif\n"
            "#if NOT_SET\nclass Ghost {}\n#endif\n")
        (self.root / "Game/Native.cs").write_text(
            "class Native {\n#if UNITY_IOS && !UNITY_EDITOR\n  void Call() {}\n#endif\n"
            "#if UNITY_2021_1_OR_NEWER || UNITY_5_6_0\n  void New() {}\n#endif\n}\n")
        from lotsman import indexer
        indexer.index_repo(self.root, self.store)
        self.project = unity.load(self.store)
        self.pd = csdefines.load_project(self.root)

    def value(self, sym, target, path="Game/Native.cs"):
        asm = self.project.assembly_for(path)[0]
        ctx = csdefines.context_for(self.pd, asm, self.store.directives_in_file(path), self.root)
        return ctx.value(sym, target)

    def test_project_facts(self):
        self.assertEqual(self.pd.groups, {"Android": {"SHOP_ANDROID", "ADS"}, "iOS": {"ADS"}})
        self.assertEqual(self.pd.rsp, {"RSP_ALL"})
        self.assertEqual(self.pd.targets, ["Editor", "iOS", "Android"])
        self.assertEqual(self.pd.settings_path, "../ProjectSettings/ProjectSettings.asset")

    def test_symbol_values(self):
        self.assertIs(self.value("UNITY_EDITOR", "Editor"), True)
        self.assertIs(self.value("UNITY_IOS", "iOS"), True)
        self.assertIsNone(self.value("UNITY_IOS", "Editor"))       # active target unknown
        self.assertIs(self.value("SHOP_ANDROID", "iOS"), False)
        self.assertIsNone(self.value("SHOP_ANDROID", "Editor"))
        self.assertIs(self.value("RSP_ALL", "iOS"), True)
        self.assertIs(self.value("UNITY_2021_1_OR_NEWER", "iOS"), True)
        self.assertIs(self.value("UNITY_5_6_0", "iOS"), False)
        self.assertIsNone(self.value("ENABLE_VR", "iOS"))           # Unity switch: unknown
        self.assertIs(self.value("NOT_SET", "Android"), False)
        self.assertIs(self.value("HAS_IAP", "Android", "Ads/Banner.cs"), True)  # versionDefines

    def test_active_target_from_csproj(self):
        (self.tmp / "Assembly-CSharp.csproj").write_text(
            "<DefineConstants>UNITY_EDITOR;UNITY_ANDROID;SHOP_ANDROID</DefineConstants>")
        self.pd = csdefines.load_project(self.root)
        self.assertEqual(self.pd.active, "Android")
        self.assertIs(self.value("UNITY_ANDROID", "Editor"), True)
        self.assertIs(self.value("SHOP_ANDROID", "Editor"), True)

    def test_unity_report(self):
        out = unity.render_unity(self.store, "Game/Native.cs", root=self.root)
        self.assertIn("compiled for: everywhere", out)
        self.assertIn("UNITY_IOS && !UNITY_EDITOR → iOS only — 1× Game/Native.cs:2", out)
        self.assertIn("UNITY_2021_1_OR_NEWER || UNITY_5_6_0 → everywhere", out)
        out = unity.render_unity(self.store, "Ads/Banner.cs", root=self.root)
        self.assertIn("compiled for: iOS + Android only", out)
        self.assertIn("HAS_IAP && UNITY_ANDROID → Android only", out)
        self.assertIn("defined nowhere: NOT_SET", out)
        self.assertIn("project defines: ../ProjectSettings/ProjectSettings.asset:3 Android = "
                      "ADS;SHOP_ANDROID; ../ProjectSettings/ProjectSettings.asset:4 iPhone = ADS; "
                      "csc.rsp = RSP_ALL", out)
        out = unity.render_unity(self.store, "Banner", root=self.root)
        self.assertIn("declared under #if HAS_IAP && UNITY_ANDROID → Android only", out)

    def test_outline_marks_only_where_the_condition_changes(self):
        path = "Game/Native.cs"
        rows = self.store.symbols_in_file(path)
        lines = outlineview.outline_rows(rows, marks=outlineview.condition_marks(
            self.store, path, rows))
        self.assertTrue(lines[0].endswith("class Native {"))
        self.assertTrue(lines[1].endswith("[#if UNITY_IOS && !UNITY_EDITOR]"))


if __name__ == "__main__":
    unittest.main()
