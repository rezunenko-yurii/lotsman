"""Caller check after a signature edit (V1)."""

from __future__ import annotations

import os
import shutil
import tempfile
import time
import unittest
from pathlib import Path

from lotsman import indexer
from lotsman.callcheck import Arity, arity, call_counts, fit
from lotsman.impact import generate_impact


def lines(text: str) -> list[str]:
    return text.splitlines()


class ArityTests(unittest.TestCase):
    def test_shapes(self):
        self.assertEqual(arity(lines("public void A(int x, string y)"), 1, "A"), Arity(2, 2))
        self.assertEqual(arity(lines("void B()"), 1, "B"), Arity(0, 0))
        self.assertEqual(arity(lines('void C(int x, string s = "a,b", int n = F(1, 2))'), 1, "C"),
                         Arity(1, 3))
        self.assertEqual(arity(lines("void D(string f, params object[] args)"), 1, "D"),
                         Arity(1, 1, variadic=True))
        self.assertEqual(arity(lines("static void E(this Node n, bool f = false)"), 1, "E"),
                         Arity(1, 2, extension=True))
        self.assertEqual(arity(lines("T G<T>(Dictionary<string, List<int>> map,\n"
                                     "        [CallerMemberName] string m = \"\",\n"
                                     "        (int a, int b) pair = default)"), 1, "G"),
                         Arity(1, 3))
        self.assertIsNone(arity(lines("public int Count { get => Get(1, 2); }"), 1, "Count"))
        # a quote inside a comment opens no string; `//` inside a string is text
        src = lines('/* only double quotes (") are used */\nvoid H(string a = "//", int b) {}')
        self.assertEqual(arity(src, 2, "H"), Arity(1, 2))
        self.assertEqual(arity(lines('void V(string p = @"a""b\n c", char q = \'"\')'), 1, "V"),
                         Arity(0, 2))

    def test_fit(self):
        two = [Arity(2, 2)]
        self.assertEqual(fit(2, two), "exact")
        self.assertEqual(fit(1, two), "breaks")
        self.assertEqual(fit(1, [Arity(1, 2)]), "defaults")
        self.assertEqual(fit(5, [Arity(1, 1, variadic=True)]), "defaults")
        self.assertEqual(fit(1, [Arity(1, 1), Arity(2, 2)]), "exact")     # overloads
        self.assertEqual(fit(1, [Arity(2, 2, extension=True)]), "exact")  # x.M(a)
        ext = [Arity(1, 2, extension=True)]
        self.assertEqual(fit(1, ext, static=False), "exact")     # x.M(a)
        self.assertEqual(fit(1, ext, static=True), "defaults")   # Ext.M(x)
        self.assertEqual(fit(None, two), "unclear")


class CallCountTests(unittest.TestCase):
    def test_counts(self):
        src = lines('Foo(a, "x, y", Bar(1, 2), (p, q) => p + q, new Dictionary<int, string>());\n'
                    "x.Foo(); y.Foo<int>(1) // Foo(1, 2, 3)\n"
                    "Foo(first,\n"
                    "    second); var g = nameof(Foo); Action a = Foo;\n"
                    "Foo(open")
        self.assertEqual(call_counts(src, 1, "Foo"), [5])
        self.assertEqual(call_counts(src, 2, "Foo"), [0, 1])  # the comment is not a call
        self.assertEqual(call_counts(src, 3, "Foo"), [2])
        self.assertEqual(call_counts(src, 4, "Foo"), [])      # nameof / method group
        self.assertEqual(call_counts(src, 5, "Foo"), [None])  # never closed


class ImpactSignatureTests(unittest.TestCase):
    """add / remove / optional / params on an indexed C# project."""

    SVC = ("public class Service {\n"
           "    public void Send(string to, string body) {}\n"
           "    public void Log(string fmt, params object[] args) {}\n"
           "}\n")

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="lotsman_v1_"))
        (self.tmp / "Service.cs").write_text(self.SVC)
        (self.tmp / "Client.cs").write_text(
            "class Client {\n"
            "    void Run(Service s) {\n"
            "        s.Send(\"a\", \"b\");\n"
            "        s.Send(\"a\",\n"
            "               \"b\");\n"
            "        s.Log(\"x\");\n"
            "        s.Log(\"x\", 1, 2);\n"
            "    }\n"
            "}\n")
        (self.tmp / "Other.cs").write_text(  # same member name, unrelated type
            "class Mail { void Send(int x) {} void Go() { Send(1); } }\n")
        self.store = indexer.open_store(self.tmp)
        indexer.index_repo(self.tmp, self.store)

    def tearDown(self):
        self.store.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def edit(self, old: str, new: str) -> str:
        path = self.tmp / "Service.cs"
        path.write_text(path.read_text().replace(old, new))
        later = time.time() + 5
        os.utime(path, (later, later))
        indexer.index_repo(self.tmp, self.store)
        return generate_impact(self.store, ["Service.cs"], root=self.tmp)

    def test_added_required_parameter_breaks_callers(self):
        out = self.edit("Send(string to, string body)", "Send(string to, string body, int prio)")
        self.assertIn("Service.Send (Service.cs:2): 2 → 3 params", out)
        self.assertIn("breaks — no overload takes this argument count: 2 calls in 1 files", out)
        self.assertIn("      Client.cs:3,4 (2 args)", out)
        self.assertNotIn("Other.cs", out.split("Changed files")[0])

    def test_removed_parameter_breaks_callers(self):
        out = self.edit("Send(string to, string body)", "Send(string to)")
        self.assertIn("2 → 1 params", out)
        self.assertIn("Client.cs:3,4 (2 args)", out)

    def test_optional_parameter_is_a_maybe(self):
        out = self.edit("Send(string to, string body)", "Send(string to, string body, int p = 0)")
        self.assertIn("2 → 2-3 params", out)
        self.assertIn("maybe — compiles only through optional/params parameters", out)
        self.assertNotIn("breaks —", out)

    def test_params_changes(self):
        out = self.edit("Log(string fmt, params object[] args)", "Log(string fmt)")
        self.assertIn("Service.Log (Service.cs:3): 1+ → 1 params", out)
        self.assertIn("Client.cs:7 (3 args)", out)            # breaks
        self.assertNotIn("Client.cs:6", out)                  # still exact

    def test_revert_and_body_edits_forget_or_keep(self):
        self.edit("Send(string to, string body)", "Send(string to)")
        self.edit("{}\n    public void Log", "{ var x = 1; }\n    public void Log")  # body edit
        self.assertEqual(len(self.store.sig_changes("Service.cs")), 1)  # baseline kept
        out = self.edit("Send(string to)", "Send(string to, string body)")
        self.assertEqual(self.store.sig_changes("Service.cs"), [])
        self.assertNotIn("Signature changes", out)

    def test_baseline_expires(self):
        from lotsman.store import SIG_CHANGE_TTL
        self.edit("Send(string to, string body)", "Send(string to)")
        later = time.time() + SIG_CHANGE_TTL + 60
        self.assertEqual(self.store.sig_changes("Service.cs", now=later), [])
        self.assertEqual(len(self.store.sig_changes("Service.cs")), 1)

    def test_other_type_receivers_are_skipped(self):
        (self.tmp / "Mixed.cs").write_text(
            "class Mixed {\n"
            "    Mail _mail;\n"
            "    void Go(Service s) {\n"
            "        _mail.Send(1, 2);\n"          # declared as another type
            "        Mail.Send(1, 2);\n"           # static call on another type
            "        s.Send(\"a\", \"b\");\n"     # Service: checked
            "    }\n"
            "}\n")
        indexer.index_repo(self.tmp, self.store)
        out = self.edit("Send(string to, string body)", "Send(string to)")
        self.assertNotIn("Mixed.cs:4", out)
        self.assertNotIn("Mixed.cs:5", out)
        self.assertIn("      Mixed.cs:6 (2 args)", out)
        self.assertIn("2 same-name calls skipped as another type's", out)


if __name__ == "__main__":
    unittest.main()
