"""`lotsman unused`: C# types and methods nothing names (X1)."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from lotsman import indexer, unused
from lotsman.store import SymbolRow

REPO = Path(__file__).resolve().parent.parent
HEADER = "%YAML 1.1\n%TAG !u! tag:unity3d.com,2011:\n"
G_HUB = "a" * 32
G_DEAD = "b" * 32
G_HUB_PREFAB = "c" * 32

HUB = """\
using UnityEngine;

public class Hub : MonoBehaviour
{
    bool _ready { get; set; }

    void Awake() { Helper(); _ready = true; Invoke("ByString", 1f); }
    void Helper() {}
    public void OnClick() {}
    public void OnOverride() {}
    void Footstep() {}
    void Orphan() {}
    void Recurse() { Recurse(); }
    void ByString() {}
    [ContextMenu("Reset all")]
    void Menu() {}
    public override string ToString() => "hub";
    public bool ShouldSerializeName() => false;
}
"""

HUB_PREFAB = HEADER + f"""\
--- !u!114 &200
MonoBehaviour:
  m_Script: {{fileID: 11500000, guid: {G_HUB}, type: 3}}
  onUse:
    m_PersistentCalls:
      m_Calls:
      - m_Target: {{fileID: 200}}
        m_TargetAssemblyTypeName:
        m_MethodName: OnClick
--- !u!74 &7400000
AnimationClip:
  m_Events:
  - functionName: Footstep
"""

SCENE = HEADER + f"""\
--- !u!1001 &10
PrefabInstance:
  m_Modification:
    m_Modifications:
    - target: {{fileID: 200, guid: {G_HUB_PREFAB}, type: 3}}
      propertyPath: onUse.m_PersistentCalls.m_Calls.Array.data[0].m_MethodName
      value: OnOverride
      objectReference: {{fileID: 0}}
  m_SourcePrefab: {{fileID: 100100000, guid: {G_HUB_PREFAB}, type: 3}}
"""

DEAD = """\
using UnityEngine;

public class Dead : MonoBehaviour
{
    void Update() {}
    public void Poke() {}
}
"""

LIB = """\
public static class Ext
{
    public static int Twice(this int x) => x * 2;
}

public interface IModule {}
public class ModuleA : IModule {}

public interface ISink { void Put(); }
public class Sink : ISink { public void Put() {} }

public class Outer
{
    public class ProbeTargetInner {}
}

public class JsonProbeTargetTail {}

public class User
{
    ISink _sink;
    public int Run()
    {
        var types = TypeCache.GetTypesDerivedFrom<IModule>();
        var t = Type.GetType("ProbeTargetInner");
        var u = AssembliesHelper.FindTypeExact("ProbeTargetTail");
        return 3.Twice();
    }
}
"""

TOOLS = """\
using UnityEditor;

public class Tools
{
    [MenuItem("Tools/Run")]
    static void Run() {}
}

public class Batch
{
    public static void BuildAll() {}
}

public class ToolsInspector : Editor
{
}
"""

PACKAGE_API = """\
public class Api
{
    public void Call() {}
    void Hidden() {}
}
"""


def _meta(path: Path, guid: str) -> None:
    Path(str(path) + ".meta").write_text(f"fileFormatVersion: 2\nguid: {guid}\n")


class UnusedTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="lotsman_x1_"))
        (self.tmp / "ProjectSettings").mkdir()
        a = self.root = self.tmp / "Assets"
        for d in ("Scripts", "Prefabs", "Scenes", "Editor", "Lib"):
            (a / d).mkdir(parents=True)
        (a / "Scripts/Hub.cs").write_text(HUB)
        _meta(a / "Scripts/Hub.cs", G_HUB)
        (a / "Scripts/Dead.cs").write_text(DEAD)
        _meta(a / "Scripts/Dead.cs", G_DEAD)
        (a / "Scripts/Lib.cs").write_text(LIB)
        (a / "Prefabs/Hub.prefab").write_text(HUB_PREFAB)
        _meta(a / "Prefabs/Hub.prefab", G_HUB_PREFAB)
        (a / "Scenes/Main.unity").write_text(SCENE)
        (a / "Editor/Tools.cs").write_text(TOOLS)
        (a / "Lib/package.json").write_text('{"name": "com.x.lib"}')
        (a / "Lib/Api.cs").write_text(PACKAGE_API)
        self.db = self.tmp / "db" / "index.db"
        self.store = indexer.Store(self.db)
        indexer.index_repo(a, self.store)

    def tearDown(self):
        self.store.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def names(self, scope: str = "") -> set[str]:
        rep = unused.collect_unused(self.store, self.root, scope)
        return {c.row.name for c in rep.candidates}

    def test_candidates(self):
        names = self.names()
        # Nothing names them: the self-call does not count; `Sink` only
        # implements `ISink`, and nothing creates it.
        self.assertTrue({"Orphan", "Recurse", "Put", "Hidden", "Sink", "User"} <= names)
        # A MonoBehaviour no asset uses, its members folded into it.
        self.assertIn("Dead", names)
        self.assertNotIn("Poke", names)
        rep = unused.collect_unused(self.store, self.root)
        dead = next(c for c in rep.candidates if c.row.name == "Dead")
        self.assertEqual(dead.members, 2)
        self.assertIn("no prefab, scene or asset", dead.note)

    def test_used_by_code_assets_and_strings(self):
        names = self.names()
        for used in ("Hub", "Helper", "_ready", "ByString",        # code, bare, Invoke("…")
                     "OnClick", "OnOverride", "Footstep",           # UnityEvent, override, clip
                     "Ext", "Twice", "ISink",                       # extension, field type
                     "Outer", "JsonProbeTargetTail"):               # nested, name tail
            self.assertNotIn(used, names, used)

    def test_may_run_without_a_name(self):
        rep = unused.collect_unused(self.store, self.root)
        names = {c.row.name for c in rep.candidates}
        for skipped in ("Awake", "Menu", "ToString", "ShouldSerializeName",
                        "Tools", "Run", "Batch", "BuildAll",       # [MenuItem], -executeMethod
                        "ToolsInspector", "ModuleA",               # Editor, GetTypesDerivedFrom
                        "Api", "Call"):                            # package API
            self.assertNotIn(skipped, names, skipped)
        self.assertEqual(rep.skipped[unused.PACKAGE], 2)
        self.assertEqual(rep.skipped[unused.EXECUTE], 1)
        self.assertGreaterEqual(rep.skipped[unused.ATTRIBUTE], 3)   # Menu, Run, Tools
        self.assertEqual(rep.attributes["MenuItem"], 1)
        text = unused.render_unused(rep)
        self.assertIn("may run without a name (not listed)", text)
        self.assertIn("[ContextMenu] 1", text)

    def test_scope_and_render(self):
        self.assertEqual(self.names("Lib"), {"Hidden"})
        self.assertEqual(self.names("./Lib/"), {"Hidden"})
        rep = unused.collect_unused(self.store, self.root, "Scripts")
        text = unused.render_unused(rep, limit=2)
        self.assertTrue(text.startswith("unused candidates under Scripts/ — "))
        self.assertIn("more (narrow with a path, or -k)", text)

    def test_attribute_parsing(self):
        lines = ["    int x;",
                 '    [TestCase(Kind.A, "a|b[0]", "f",',
                 '        TestName = "X")]',
                 '    [Category("slow")] // note',
                 "    public void Check(Kind kind) { }",
                 '    [MenuItem("Tools/A")] static void Go() {}']
        self.assertEqual(unused._attributes(lines, SymbolRow("a.cs", "Check", "method", 5, 5, "")),
                         ["TestCase", "Category"])
        self.assertEqual(unused._attributes(lines, SymbolRow("a.cs", "Go", "method", 6, 6, "")),
                         ["MenuItem"])

    def test_cli_json(self):
        env = {**os.environ, "LOTSMAN_INDEX": str(self.db), "LOTSMAN_QUERYLOG": "0",
               "PYTHONPATH": str(REPO)}
        out = subprocess.run([sys.executable, "-m", "lotsman", "--repo", str(self.root),
                              "unused", "Scripts", "--json"],
                             capture_output=True, text=True, env=env, cwd=REPO, check=True)
        data = json.loads(out.stdout)
        self.assertEqual(data["scope"], "Scripts")
        self.assertIn({"path": "Scripts/Hub.cs", "name": "Orphan", "kind": "method",
                       "line": 12, "members": 0, "public": False}, data["candidates"])
        self.assertIn(unused.MESSAGE, data["not_listed"])


class NonUnityTests(unittest.TestCase):
    def test_plain_csharp_repo(self):
        tmp = Path(tempfile.mkdtemp(prefix="lotsman_x1_plain_"))
        try:
            (tmp / "Alpha.cs").write_text("class Alpha\n{\n    void Used() {}\n"
                                          "    void Run() { Used(); }\n}\n\nclass Beta\n{\n}\n")
            store = indexer.Store(tmp / "db" / "index.db")
            indexer.index_repo(tmp, store)
            rep = unused.collect_unused(store, tmp)
            self.assertEqual({c.row.name for c in rep.candidates}, {"Alpha", "Beta"})
            a = next(c for c in rep.candidates if c.row.name == "Alpha")
            self.assertEqual((a.members, a.note), (2, ""))
            store.close()
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
