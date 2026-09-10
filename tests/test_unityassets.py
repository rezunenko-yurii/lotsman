"""Serialized Unity assets as references (U1)."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

from lotsman import indexer, unity, unityassets
from lotsman.changeview import render_change
from lotsman.impact import generate_impact
from lotsman.refsview import render_refs

REPO = Path(__file__).resolve().parent.parent
G_DOOR = "d" * 32          # Scripts/Door.cs
G_BASE = "b" * 32          # Scripts/Interactable.cs
G_SWITCH = "5" * 32        # Scripts/Switch.cs
G_DOOR_PREFAB = "e" * 32   # Prefabs/Door.prefab
G_GONE = "0" * 31 + "1"    # a script whose .cs was deleted
G_CHANNEL = "c" * 32       # ScriptableObjects/OnOpen.asset


def _meta(path: Path, guid: str) -> None:
    Path(str(path) + ".meta").write_text(f"fileFormatVersion: 2\nguid: {guid}\nMonoImporter:\n")


HEADER = "%YAML 1.1\n%TAG !u! tag:unity3d.com,2011:\n"

# Door prefab: a Door component (fields, a UnityEvent to Switch.Toggle by type
# name and to Door.Open through the local target), a Switch component.
DOOR_PREFAB = HEADER + f"""\
--- !u!1 &100
GameObject:
  m_Name: Door
--- !u!114 &200
MonoBehaviour:
  m_ObjectHideFlags: 0
  m_GameObject: {{fileID: 100}}
  m_Enabled: 1
  m_Script: {{fileID: 11500000, guid: {G_DOOR}, type: 3}}
  m_Name:
  m_EditorClassIdentifier:
  speed: 2
  _label: open
  onUse:
    m_PersistentCalls:
      m_Calls:
      - m_Target: {{fileID: 300}}
        m_TargetAssemblyTypeName: Switch, Assembly-CSharp
        m_MethodName: Toggle
        m_Mode: 1
      - m_Target: {{fileID: 200}}
        m_TargetAssemblyTypeName:
        m_MethodName: set_IsOpen
        m_Mode: 6
--- !u!114 &300
MonoBehaviour:
  m_GameObject: {{fileID: 100}}
  m_Script: {{fileID: 11500000, guid: {G_SWITCH}, type: 3}}
  m_Name:
  on: 1
"""

# Scene: an instance of the Door prefab, a stripped Door component other
# objects point at, a component whose script is gone, a stale call, a call
# without a target, a call into a ScriptableObject asset.
MAIN_SCENE = HEADER + f"""\
--- !u!1001 &400
PrefabInstance:
  m_ObjectHideFlags: 0
  m_SourcePrefab: {{fileID: 100100000, guid: {G_DOOR_PREFAB}, type: 3}}
--- !u!114 &500 stripped
MonoBehaviour:
  m_CorrespondingSourceObject: {{fileID: 200, guid: {G_DOOR_PREFAB}, type: 3}}
  m_PrefabInstance: {{fileID: 400}}
  m_Script: {{fileID: 11500000, guid: {G_DOOR}, type: 3}}
--- !u!114 &600
MonoBehaviour:
  m_Script: {{fileID: 11500000, guid: {G_GONE}, type: 3}}
  lost: 1
--- !u!114 &700
MonoBehaviour:
  m_Script: {{fileID: 11500000, guid: {G_SWITCH}, type: 3}}
  on: 0
  onPress:
    m_PersistentCalls:
      m_Calls:
      - m_Target: {{fileID: 500}}
        m_TargetAssemblyTypeName:
        m_MethodName: Slam
        m_Mode: 1
      - m_Target: {{fileID: 0}}
        m_MethodName: Toggle
        m_Mode: 1
      - m_Target: {{fileID: 11400000, guid: {G_CHANNEL}, type: 2}}
        m_TargetAssemblyTypeName:
        m_MethodName: Raise
        m_Mode: 1
      - m_Target: {{fileID: 500}}
        m_TargetAssemblyTypeName:
        m_MethodName: set_enabled
        m_Mode: 6
"""

CHANNEL_ASSET = HEADER + f"""\
--- !u!114 &11400000
MonoBehaviour:
  m_Script: {{fileID: 11500000, guid: {"a" * 32}, type: 3}}
  m_Name: OnOpen
  description: opened
"""

CLIP = HEADER + """\
--- !u!74 &7400000
AnimationClip:
  m_Name: Open
  m_Events:
  - time: 0.5
    functionName: PlaySound
    data:
  - time: 0.9
    functionName: Vanished
    data:
"""


class ParseTests(unittest.TestCase):
    def rows(self, text: str):
        return {(r.kind, r.key, r.owner): (r.line, r.count)
                for r in unityassets.parse_asset(text.encode())}

    def test_prefab(self):
        rows = self.rows(DOOR_PREFAB)
        self.assertEqual(rows[("script", G_DOOR, "")][0], 11)
        self.assertIn(("field", "speed", "S:" + G_DOOR), rows)
        self.assertIn(("field", "onUse", "S:" + G_DOOR), rows)
        self.assertNotIn(("field", "m_Enabled", "S:" + G_DOOR), rows)
        # The target component's script wins over the serialized type name.
        self.assertEqual(rows[("call", "Toggle", "S:" + G_SWITCH)][0], 21)
        self.assertIn(("call", "IsOpen", "S:" + G_DOOR), rows)   # set_IsOpen

    def test_scene(self):
        rows = self.rows(MAIN_SCENE)
        self.assertIn(("prefab", G_DOOR_PREFAB, ""), rows)
        self.assertIn(("script", G_DOOR, ""), rows)                  # stripped: a use
        self.assertNotIn(("field", "m_CorrespondingSourceObject", "S:" + G_DOOR), rows)
        self.assertIn(("call", "Slam", "S:" + G_DOOR), rows)         # via stripped target
        self.assertIn(("call", "Toggle", "X:"), rows)                # no target object
        self.assertIn(("call", "Raise", "A:" + G_CHANNEL), rows)

    def test_prefab_instance_overrides(self):
        # A call set on a prefab instance is an override (one per instance
        # and event slot); its `target` line wraps.
        slot = "m_OnClick.m_PersistentCalls.m_Calls.Array.data[0]"
        mod = ("    - target: {{fileID: 7745, guid: {g},\n        type: 3}}\n"
               "      propertyPath: {slot}.{prop}\n      value: {value}\n"
               "      objectReference: {{fileID: {ref}}}\n")

        def instance(fid: str, *mods: tuple[str, str, str]) -> str:
            return (f"--- !u!1001 &{fid}\nPrefabInstance:\n  m_Modification:\n"
                    "    m_Modifications:\n"
                    + "".join(mod.format(g=G_DOOR_PREFAB, slot=slot, prop=p, value=v, ref=r)
                              for p, v, r in mods)
                    + f"  m_SourcePrefab: {{fileID: 100100000, guid: {G_DOOR_PREFAB}, type: 3}}\n")
        text = HEADER + instance(
            "10", ("m_TargetAssemblyTypeName", "Door, Assembly-CSharp", "0"),
            ("m_MethodName", "Open", "0")) + instance(
            "20", ("m_Target", "", "30"), ("m_MethodName", "set_IsOpen", "0")) + instance(
            "40", ("m_Target", "", "0"), ("m_MethodName", "Slam", "0")) + (
            f"--- !u!114 &30 stripped\nMonoBehaviour:\n"
            f"  m_Script: {{fileID: 11500000, guid: {G_SWITCH}, type: 3}}\n")
        rows = self.rows(text)
        self.assertEqual(rows[("call", "Open", "T:Door")][0], 15)    # the value line
        self.assertIn(("call", "IsOpen", "S:" + G_SWITCH), rows)     # local target
        # A null target in an override may just mean "not overridden": unknown.
        self.assertIn(("call", "Slam", ""), rows)

    def test_clip_meta_binary_and_langs(self):
        self.assertIn(("anim", "PlaySound", ""), self.rows(CLIP))
        meta = b"ModelImporter:\n  clipAnimations:\n  - events:\n    - functionName: Step\n"
        self.assertEqual([(r.key, r.line) for r in unityassets.parse_model_meta(meta)],
                         [("Step", 4)])
        self.assertEqual(unityassets.parse_asset(b"\x00\x01binary"), [])
        self.assertEqual(unityassets.lang_for("A/B.prefab"), unityassets.ASSET_LANG)
        self.assertEqual(unityassets.lang_for("A/X.overrideController"), unityassets.ASSET_LANG)
        self.assertEqual(unityassets.lang_for("A/B.cs.meta"), unityassets.META_LANG)
        self.assertEqual(unityassets.lang_for("A/Pig.FBX.meta"), unityassets.META_LANG)
        self.assertIsNone(unityassets.lang_for("A/Tex.png.meta"))
        self.assertIsNone(unityassets.lang_for("A/Folder.meta"))


class ProjectTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="lotsman_u1_"))
        (self.tmp / "ProjectSettings").mkdir()
        a = self.root = self.tmp / "Assets"
        (a / "Scripts").mkdir(parents=True)
        (a / "Prefabs").mkdir()
        (a / "Scenes").mkdir()
        (a / "Anim").mkdir()
        (a / "ScriptableObjects").mkdir()
        (a / "Scripts/Interactable.cs").write_text(
            "public class Interactable : MonoBehaviour {\n    public float speed;\n}\n")
        _meta(a / "Scripts/Interactable.cs", G_BASE)
        (a / "Scripts/Door.cs").write_text(
            "public class Door : Interactable {\n"
            "    public string _label;\n"
            "    public bool IsOpen { get; set; }\n"
            "    public void Open() {}\n"
            "    public void PlaySound() {}\n"
            "}\n")
        _meta(a / "Scripts/Door.cs", G_DOOR)
        (a / "Scripts/Switch.cs").write_text(
            "public class Switch : MonoBehaviour {\n    public bool on;\n"
            "    public void Toggle() {}\n}\n")
        _meta(a / "Scripts/Switch.cs", G_SWITCH)
        (a / "Scripts/Mail.cs").write_text(  # same member name, unrelated type
            "public class Mail { public float speed; public void Toggle() {} }\n")
        (a / "Prefabs/Door.prefab").write_text(DOOR_PREFAB)
        _meta(a / "Prefabs/Door.prefab", G_DOOR_PREFAB)
        (a / "Scenes/Main.unity").write_text(MAIN_SCENE)
        (a / "ScriptableObjects/OnOpen.asset").write_text(CHANNEL_ASSET)
        _meta(a / "ScriptableObjects/OnOpen.asset", G_CHANNEL)
        (a / "Anim/Open.anim").write_text(CLIP)
        (a / "Anim/Binary.asset").write_bytes(b"\x00\x00\x00\x01binary lighting data")
        self.store = indexer.open_store(self.root)
        indexer.index_repo(self.root, self.store)

    def tearDown(self):
        self.store.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_refs_type_lists_script_users_and_nested(self):
        out = render_refs(self.store, "Door")
        self.assertIn("assets — 2 files use the script (m_Script by GUID):", out)
        self.assertIn("  Prefabs/Door.prefab:11", out)
        self.assertIn("  Scenes/Main.unity:", out)
        self.assertIn("(bound by", render_change(self.store, "Door"))

    def test_refs_members(self):
        out = render_refs(self.store, "Toggle")
        self.assertIn("assets — 2 UnityEvent calls name it", out)
        self.assertIn("Prefabs/Door.prefab:21  Switch", out)
        self.assertIn("Scenes/Main.unity:", out)       # the target-less one, owner unknown
        self.assertIn("owner not recorded", out)
        self.assertIn("  Prefabs/Door.prefab:25  Door", render_refs(self.store, "IsOpen"))
        sound = render_refs(self.store, "PlaySound")
        self.assertIn("animation events name it", sound)
        self.assertIn("Anim/Open.anim:", sound)

    def test_inherited_fields_and_owner_filter(self):
        out = render_refs(self.store, "Interactable.speed")
        self.assertIn("files serialize it as a field", out)
        self.assertIn("Prefabs/Door.prefab:", out)          # Door inherits speed
        out = render_change(self.store, "Mail.speed")
        self.assertNotIn("serialize it", out)               # Mail is not a component here
        out = render_change(self.store, "Mail.Toggle")
        self.assertNotIn("Prefabs/Door.prefab:21", out)     # the call targets Switch

    def test_impact_names_assets(self):
        out = generate_impact(self.store, ["Scripts/Door.cs"], root=self.root)
        self.assertIn("Assets (Unity;", out)
        self.assertIn("Scripts/Door.cs: script in 2 assets", out)
        self.assertIn("IsOpen (Prefabs/Door.prefab:25)", out)
        self.assertIn("fields serialized in 1 assets", out)

    def test_missing(self):
        out = unity.render_unity(self.store, "missing", root=self.root)
        self.assertIn("unresolved scripts — 2 GUIDs", out)  # G_GONE and the SO's script
        self.assertIn(G_GONE, out)
        self.assertIn("Scenes/Main.unity:", out)
        self.assertIn("stale UnityEvent calls — 1", out)
        self.assertIn("Door.Slam", out)
        self.assertNotIn("enabled", out)                    # Unity's own member
        self.assertIn("without a target object — 1", out)
        self.assertIn("Vanished: Anim/Open.anim:", out)
        self.assertNotIn("PlaySound:", out)

    def test_meta_written_later_is_picked_up(self):
        (self.root / "Scripts/Late.cs").write_text("public class Late : MonoBehaviour {}\n")
        (self.root / "Prefabs/Late.prefab").write_text(HEADER + (
            "--- !u!114 &1\nMonoBehaviour:\n"
            "  m_Script: {fileID: 11500000, guid: " + "7" * 32 + ", type: 3}\n"))
        indexer.index_repo(self.root, self.store)
        self.assertNotIn("assets —", render_refs(self.store, "Late"))
        _meta(self.root / "Scripts/Late.cs", "7" * 32)     # Unity imports it later
        indexer.index_repo(self.root, self.store)
        self.assertIn("Prefabs/Late.prefab:5", render_refs(self.store, "Late"))

    def test_json_outputs(self):
        env = {**os.environ, "LOTSMAN_QUERYLOG": "0", "PYTHONPATH": str(REPO)}
        run = lambda *a: json.loads(subprocess.run(  # noqa: E731
            [sys.executable, "-m", "lotsman", "--repo", str(self.root), *a],
            capture_output=True, text=True, env=env, cwd=REPO).stdout)
        refs = run("refs", "--json", "Door")
        self.assertEqual([s["path"] for s in refs["assets"]["scripts"]],
                         ["Prefabs/Door.prefab", "Scenes/Main.unity"])
        change = run("change", "--json", "Switch.Toggle")
        self.assertEqual(change["assets"]["unity_events"][0]["path"], "Prefabs/Door.prefab")
        self.assertIn("prefab-instance overrides", change["not_covered"])

    def test_edit_and_delete_refresh_rows(self):
        prefab = self.root / "Prefabs/Door.prefab"
        prefab.write_text(DOOR_PREFAB.replace("set_IsOpen", "set_Other"))
        later = time.time() + 5
        os.utime(prefab, (later, later))
        indexer.index_repo(self.root, self.store)
        self.assertNotIn("UnityEvent", render_refs(self.store, "IsOpen"))
        prefab.unlink()
        indexer.index_repo(self.root, self.store)
        out = render_refs(self.store, "Door")
        self.assertIn("assets — 1 files use the script", out)
        self.assertNotIn("Prefabs/Door.prefab", out)


class NonUnityTests(unittest.TestCase):
    def test_prefab_outside_unity_is_not_indexed(self):
        tmp = Path(tempfile.mkdtemp(prefix="lotsman_u1_plain_"))
        try:
            (tmp / "a.py").write_text("def door():\n    pass\n")
            (tmp / "x.prefab").write_text(DOOR_PREFAB)
            store = indexer.open_store(tmp)
            indexer.index_repo(tmp, store)
            self.assertEqual(set(store.known_files()), {"a.py"})
            self.assertNotIn("assets", render_refs(store, "door"))
            self.assertIn("not covered: reflection by string name\n",
                          render_change(store, "door") + "\n")
            store.close()
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
