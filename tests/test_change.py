"""`change TARGET`: definition, implementations, family-gated callers."""

import contextlib
import io
import json
import shutil
import tempfile
import unittest
from pathlib import Path

from lotsman import changeview, extract, indexer
from lotsman.cli import main

FILES = {
    "Gates/IShowGate.cs": (
        "public interface IShowGate {\n"
        "    bool CanShow(Request request, out string reason);\n"
        "}\n"
        "public sealed class AlwaysShowGate : IShowGate {\n"
        "    public bool CanShow(Request request, out string reason) { reason = null; return true; }\n"
        "}\n"),
    "Gates/FlowGate.cs": (
        "public class FlowGate : BaseGate {\n"
        "    public override bool CanShow(Request request, out string reason) { reason = null; return false; }\n"
        "}\n"
        "public abstract class BaseGate : IShowGate {\n"
        "    public abstract bool CanShow(Request request, out string reason);\n"
        "}\n"),
    "Windows/Sequence.cs": (
        "public class Sequence {\n"
        "    private IShowGate _gate;\n"
        "    public void Next(Request r) {\n"
        "        if (_gate.CanShow(r, out var why)) { }\n"
        "    }\n"
        "}\n"),
    "Policy/FrequencyPolicy.cs": (
        "public class FrequencyPolicy {\n"
        "    public bool CanShow(string id) => true;\n"
        "    public void Tick() { CanShow(\"x\"); }\n"
        "}\n"),
    "Tests/Editor/GateTests.cs": (
        "public class GateTests {\n"
        "    class FakeGate : IShowGate { public bool CanShow(Request r, out string w) { w = null; return true; } }\n"
        "    void Runs() { IShowGate g = new FakeGate(); g.CanShow(null, out _); }\n"
        "}\n"),
    "Ext/InjectExtensions.cs": (
        "public static class InjectExtensions {\n"
        "    public static void InjectFields(this object target) { }\n"
        "}\n"),
    "Ui/Panel.cs": (
        "public class Panel {\n"
        "    void Awake() { this.InjectFields(); }\n"
        "}\n"),
}


class TestBases(unittest.TestCase):
    def test_base_list_shapes(self):
        src = (b"public class A : B<C>, Ns.IFoo { }\n"
               b"public record R(int X) : Base(X);\n"
               b"interface I : IBar {}\n")
        _, _, bases = extract.extract_refs_full("csharp", src)
        self.assertEqual(sorted(bases), [("A", "B", 1), ("A", "IFoo", 1),
                                         ("I", "IBar", 3), ("R", "Base", 2)])


class TestChange(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="lotsman_change_"))
        for rel, text in FILES.items():
            (self.tmp / rel).parent.mkdir(parents=True, exist_ok=True)
            (self.tmp / rel).write_text(text)
        self.store = indexer.open_store(self.tmp)
        indexer.index_repo(self.tmp, self.store)

    def tearDown(self):
        self.store.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_interface_member_family(self):
        rep = changeview.collect_change(self.store, "IShowGate.CanShow")
        self.assertEqual([(r.path, r.line) for r in rep.defs], [("Gates/IShowGate.cs", 2)])
        impls = {(i.type_name, i.row.path) for i in rep.impls}
        # direct, through an abstract base, and a test double
        self.assertEqual(impls, {("AlwaysShowGate", "Gates/IShowGate.cs"),
                                 ("BaseGate", "Gates/FlowGate.cs"),
                                 ("FlowGate", "Gates/FlowGate.cs"),
                                 ("FakeGate", "Tests/Editor/GateTests.cs")})
        callers = {s.path: s.lines for s in rep.callers}
        self.assertEqual(callers["Windows/Sequence.cs"], [4])
        self.assertIn("Tests/Editor/GateTests.cs", callers)
        # FrequencyPolicy.CanShow is another method: counted, never listed
        self.assertNotIn("Policy/FrequencyPolicy.cs", callers)
        self.assertEqual(rep.name_only, 1)

    def test_override_sees_declaring_supertypes(self):
        rep = changeview.collect_change(self.store, "FlowGate.CanShow")
        declared = {(i.type_name, i.row.path) for i in rep.declared_by}
        self.assertEqual(declared, {("BaseGate", "Gates/FlowGate.cs"),
                                    ("IShowGate", "Gates/IShowGate.cs")})

    def test_extension_method_callers_are_not_gated(self):
        rep = changeview.collect_change(self.store, "InjectExtensions.InjectFields")
        self.assertTrue(rep.extension)
        self.assertIn("Ui/Panel.cs", {s.path for s in rep.callers})

    def test_type_target_lists_subtypes_and_mentions(self):
        rep = changeview.collect_change(self.store, "IShowGate")
        self.assertIsNone(rep.member)
        self.assertEqual({t for t, _, _ in rep.subtypes},
                         {"AlwaysShowGate", "BaseGate", "FlowGate", "FakeGate"})
        self.assertIn("Windows/Sequence.cs", {s.path for s in rep.callers})

    def test_render_is_compact_and_honest(self):
        out = changeview.render_change(self.store, "IShowGate.CanShow")
        self.assertIn("overrides / implementations (4):", out)
        self.assertIn("Tests/Editor/GateTests.cs", out)
        self.assertIn("[test]", out)
        self.assertIn("name-only: 1 more files use `CanShow`", out)
        self.assertIn("not covered: asset (prefab/scene) references", out)
        self.assertNotIn("FrequencyPolicy.cs", out)
        self.assertEqual(changeview.render_change(self.store, "Nope.Nothing"),
                         "(`Nope.Nothing` not found in index)")

    def test_cli_json(self):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(io.StringIO()):
            code = main(["--repo", str(self.tmp), "change", "IShowGate.CanShow", "--json"])
        self.assertEqual(code, 0)
        data = json.loads(buf.getvalue())
        self.assertEqual(data["owner"], "IShowGate")
        self.assertEqual(data["name_only_files"], 1)
        self.assertTrue(any(c["test"] for c in data["callers"]))
        self.assertEqual(data["confidence"]["resolution"], "name-based")


if __name__ == "__main__":
    unittest.main()
