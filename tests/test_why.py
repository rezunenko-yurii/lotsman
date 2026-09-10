"""`lotsman why A B`: shortest reference route with the names on each hop (X2)."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from lotsman import indexer, why

REPO = Path(__file__).resolve().parent.parent
HEADER = "%YAML 1.1\n%TAG !u! tag:unity3d.com,2011:\n"
G_HERO = "a" * 32
G_HERO_PREFAB = "b" * 32

FILES = {
    "Scripts/Core/Health.cs": """\
public class Health : MonoBehaviour
{
    public int Current;
    public void TakeDamage(int amount) { Current -= amount; }
}
""",
    "Scripts/Core/Damageable.cs": """\
public class Damageable : MonoBehaviour
{
    Health _health;

    public void Hit(int n)
    {
        _health.TakeDamage(n);
    }
}
""",
    "Scripts/Unit.cs": "public class Unit : MonoBehaviour { }\n",
    "Scripts/Player.cs": """\
public class Player : Unit
{
    Damageable _body;

    void Update()
    {
        _body.Hit(1);
    }
}
""",
    "Scripts/Enemy.cs": "public class Enemy : Unit { }\n",
    # `Caption` of another type: the file never names `Label`.
    "Scripts/UI/Label.cs": "public class Label\n{\n    public string Caption;\n}\n",
    "Scripts/Ui.cs": """\
public class Ui
{
    void Show(TMP_Text t)
    {
        t.Caption = "x";
    }
}
""",
    # A private nested `Slot` vs a property `Slot` elsewhere.
    "Scripts/Selector.cs": """\
public class Selector
{
    class Slot { }
}
""",
    "Scripts/Bag.cs": """\
public class Bag
{
    void F(Inventory s)
    {
        var x = s.Slot;
    }
}
""",
    # Each module has its own nested `Codes`.
    "Scripts/ModuleA.cs": """\
public class ModuleA
{
    public static class Codes
    {
        public const string Broken = "a";
    }
}
""",
    "Scripts/ModuleB.cs": """\
public class ModuleB
{
    public static class Codes
    {
        public const string Broken = "b";
    }

    string F()
    {
        return Codes.Broken;
    }
}
""",
    "Scripts/Ext/FloatExt.cs": """\
public static class FloatExt
{
    public static float Magnitude(this float v) => v;
}
""",
    "Scripts/MathUser.cs": """\
public class MathUser
{
    float F(float x)
    {
        return Mathf.Magnitude(x);
    }
}
""",
    "Scripts/ExtUser.cs": """\
public class ExtUser
{
    float F(float x)
    {
        return x.Magnitude();
    }
}
""",
    "Scripts/IWeapon.cs": "public interface IWeapon\n{\n    void Fire();\n}\n",
    "Scripts/Sword.cs": "public class Sword : IWeapon\n{\n    public void Fire() { }\n}\n",
    "Scripts/Clock.cs": "public class Clock\n{\n}\n",
    "Tests/FakeWeapon.cs": """\
public class FakeWeapon : IWeapon
{
    Clock _clock;
    public void Fire() { }
}
""",
    "Scripts/Hero.cs": """\
public class Hero : MonoBehaviour
{
    IWeapon _weapon;

    public void Attack()
    {
        _weapon.Fire();
    }
}
""",
    "Scripts/Window.cs": "public class Window\n{\n}\n",
    "Scripts/Menu.cs": "public class Menu\n{\n    Window _w;\n}\n",
    "Scripts/Two.cs": """\
public class First
{
    Health _h;
}

public class Second
{
    Label _l;
}
""",
    # An asmdef that does not reference Assembly-CSharp: `Player` there is
    # another assembly's type.
    "Lib/Lib.asmdef": '{"name": "Lib"}',
    "Lib/Logger.cs": """\
public class Logger
{
    Player _p;
}
""",
    "Scripts/Boot.cs": "public class Boot\n{\n    Logger _log;\n}\n",
    "Prefabs/Hero.prefab": HEADER + f"""\
--- !u!114 &200
MonoBehaviour:
  m_Script: {{fileID: 11500000, guid: {G_HERO}, type: 3}}
  onUse:
    m_PersistentCalls:
      m_Calls:
      - m_Target: {{fileID: 200}}
        m_TargetAssemblyTypeName:
        m_MethodName: Attack
""",
    "Scenes/Main.unity": HEADER + f"""\
--- !u!1001 &10
PrefabInstance:
  m_Modification:
    m_Modifications: []
  m_SourcePrefab: {{fileID: 100100000, guid: {G_HERO_PREFAB}, type: 3}}
""",
}
METAS = {"Scripts/Hero.cs": G_HERO, "Prefabs/Hero.prefab": G_HERO_PREFAB}


class WhyUnityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="lotsman_x2_"))
        (cls.tmp / "ProjectSettings").mkdir()
        cls.root = cls.tmp / "Assets"
        for rel, text in FILES.items():
            path = cls.root / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text)
        for rel, guid in METAS.items():
            (cls.root / (rel + ".meta")).write_text(f"fileFormatVersion: 2\nguid: {guid}\n")
        cls.db = cls.tmp / "db" / "index.db"
        cls.store = indexer.Store(cls.db)
        indexer.index_repo(cls.root, cls.store)

    @classmethod
    def tearDownClass(cls):
        cls.store.close()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def find(self, a: str, b: str) -> why.WhyResult:
        return why.find_path(self.store, a, b, self.root)

    def route(self, a: str, b: str) -> list[str]:
        return [h.dst for h in self.find(a, b).hops]

    def test_shortest_route_with_names_and_lines(self):
        res = self.find("Scripts/Player.cs", "Scripts/Core/Health.cs")
        self.assertEqual([h.dst for h in res.hops],
                         ["Scripts/Core/Damageable.cs", "Scripts/Core/Health.cs"])
        self.assertEqual(res.hops[0].names, {"Damageable": [3], "Hit": [7]})
        self.assertEqual(res.hops[1].names, {"Health": [3], "TakeDamage": [7]})
        text = why.render_why(res)
        self.assertIn("Scripts/Player.cs → Scripts/Core/Health.cs: 2 hops", text)
        self.assertIn("↳ names Damageable :3, Hit :7", text)
        self.assertNotIn("note:", text)

    def test_member_needs_its_type(self):
        self.assertEqual(self.route("Scripts/Ui.cs", "Scripts/UI/Label.cs"), [])

    def test_private_nested_and_own_nested_types(self):
        self.assertEqual(self.route("Scripts/Bag.cs", "Scripts/Selector.cs"), [])
        self.assertEqual(self.route("Scripts/ModuleB.cs", "Scripts/ModuleA.cs"), [])

    def test_extension_method_needs_an_instance_call(self):
        self.assertEqual(self.route("Scripts/MathUser.cs", "Scripts/Ext/FloatExt.cs"), [])
        res = self.find("Scripts/ExtUser.cs", "Scripts/Ext/FloatExt.cs")
        self.assertEqual(res.hops[0].names, {"Magnitude": [5]})

    def test_assembly_visibility(self):
        # Lib cannot compile against Assembly-CSharp: its `Player` is another type.
        self.assertEqual(self.route("Lib/Logger.cs", "Scripts/Player.cs"), [])

    def test_dispatch(self):
        res = self.find("Hero", "Sword")
        self.assertEqual(res.phase, why.DISPATCH)
        self.assertEqual([h.kind for h in res.hops], [why.NAMES, why.IMPL])
        self.assertIn("note: only through an interface", why.render_why(res))
        # Not into test doubles, unless the target is one.
        self.assertEqual(self.route("Hero", "Clock"), [])
        self.assertEqual(self.route("Hero", "FakeWeapon"),
                         ["Scripts/IWeapon.cs", "Tests/FakeWeapon.cs"])
        # A base-list hop is not a call: no route to a sibling subclass.
        self.assertEqual(self.route("Player", "Enemy"), [])

    def test_assets(self):
        res = self.find("Scenes/Main.unity", "Hero.Attack")
        self.assertEqual([(h.kind, h.dst) for h in res.hops],
                         [(why.PREFAB, "Prefabs/Hero.prefab"), (why.SCRIPT, "Scripts/Hero.cs")])
        self.assertEqual(res.hops[1].calls, {"Attack"})
        text = why.render_why(res)
        self.assertIn("↳ contains prefab Hero :", text)
        self.assertIn("↳ UnityEvent calls Attack :", text)
        full = self.find("Prefabs/Hero.prefab", "Scripts/Hero.cs")
        self.assertIn("uses script Hero :5; UnityEvent calls Attack :11", why.render_why(full))

    def test_generic_names_are_a_last_resort(self):
        res = self.find("Menu", "Window")
        self.assertEqual(res.phase, why.WEAK)
        self.assertIn("likely a name clash", why.render_why(res))

    def test_reverse_direction(self):
        res = self.find("Health", "Player")
        self.assertTrue(res.reverse)
        text = why.render_why(res)
        self.assertTrue(text.startswith(
            "Health does not reach Player by name; the other way round:"))
        self.assertIn("Player → Health: 2 hops", text)

    def test_type_endpoints_use_their_own_lines(self):
        self.assertEqual(self.route("First", "Label"), [])
        self.assertEqual(self.route("Second", "Label"), ["Scripts/UI/Label.cs"])

    def test_endpoints(self):
        self.assertEqual(self.route("Scripts/Core", "Scripts/UI"), [])
        res = self.find("Scripts/Player.cs", "Scripts/Core/")
        self.assertEqual(res.b.label, "Scripts/Core/")
        self.assertEqual([h.dst for h in res.hops], ["Scripts/Core/Damageable.cs"])
        res = self.find("Boot", "Lib")
        self.assertEqual((res.b.label, [h.dst for h in res.hops]),
                         ("Lib (assembly)", ["Lib/Logger.cs"]))
        self.assertIn("is not a file, folder, assembly or symbol", self.find("Nope", "Hero").error)
        self.assertIn("both are in", self.find("First", "Second").error)

    def test_cli(self):
        env = {**os.environ, "LOTSMAN_INDEX": str(self.db), "LOTSMAN_QUERYLOG": "0",
               "PYTHONPATH": str(REPO)}

        def run(*args):
            return subprocess.run([sys.executable, "-m", "lotsman", "--repo", str(self.root),
                                   "why", *args], capture_output=True, text=True, env=env,
                                  cwd=REPO)

        out = run("Player", "Health", "--json")
        self.assertEqual(out.returncode, 0, out.stderr)
        data = json.loads(out.stdout)
        self.assertEqual((data["direction"], data["via"]), ("forward", "names"))
        self.assertEqual(data["hops"][1]["names"][0], {"name": "Health", "lines": [3]})
        miss = run("Ui", "Label")
        self.assertEqual(miss.returncode, 1)
        self.assertIn("no reference path", miss.stdout)


class WhyPythonTests(unittest.TestCase):
    def test_functions_need_their_module_imported(self):
        tmp = Path(tempfile.mkdtemp(prefix="lotsman_x2_py_"))
        try:
            pkg = tmp / "pkg"
            pkg.mkdir()
            (pkg / "a.py").write_text("from pkg.b import helper\n\n\ndef run():\n    helper()\n")
            (pkg / "b.py").write_text("def helper():\n    pass\n")
            (pkg / "c.py").write_text("def helper():\n    pass\n")
            (pkg / "d.py").write_text("class Store:\n    def close(self):\n        pass\n")
            (pkg / "e.py").write_text("def shut(x):\n    x.close()\n")
            store = indexer.Store(tmp / "db" / "index.db")
            indexer.index_repo(tmp, store)
            res = why.find_path(store, "pkg/a.py", "pkg/b.py", tmp)
            self.assertEqual([(h.dst, list(h.names)) for h in res.hops],
                             [("pkg/b.py", ["helper"])])
            self.assertEqual(why.find_path(store, "pkg/a.py", "pkg/c.py", tmp).hops, [])
            # A method counts only where its class is named.
            self.assertEqual(why.find_path(store, "pkg/e.py", "pkg/d.py", tmp).hops, [])
            store.close()
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
