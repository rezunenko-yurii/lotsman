"""Extraction tests, including per-language regression fixtures."""

import unittest

from lotsman import extract


class TestExtractPython(unittest.TestCase):
    def test_python_symbols(self):
        src = b"class Foo:\n    def bar(self):\n        pass\n\ndef baz():\n    pass\n"
        syms = extract.extract_symbols("python", src)
        names = {(s.name, s.kind, s.line) for s in syms}
        self.assertIn(("Foo", "class", 1), names)
        self.assertIn(("baz", "function", 5), names)
        foo = next(s for s in syms if s.name == "Foo")
        self.assertEqual(foo.signature, "class Foo:")
        self.assertEqual(foo.end_line, 3)

    def test_fallback_no_newline_bleed(self):
        # blank line before def must not shift the reported line/signature
        src = b"\nclass Foo:\n    pass\n\ndef bar():\n    pass\n"
        syms = extract._extract_symbols_fallback(src)
        by_name = {s.name: s for s in syms}
        self.assertEqual(by_name["Foo"].line, 2)
        self.assertEqual(by_name["bar"].line, 5)
        self.assertEqual(by_name["bar"].signature, "def bar():")

    def test_idents_counting(self):
        counts = extract.extract_idents(b"alpha beta alpha if class gamma_delta")
        self.assertEqual(counts["alpha"], 2)
        self.assertNotIn("if", counts)  # stopword
        self.assertNotIn("class", counts)
        self.assertIn("gamma_delta", counts)

    def test_refs_precision(self):
        # Parameters, comments and string literals must not count as references.
        src = (b"def run(prepare_arg, request):\n"
               b"    # helper_func mentioned in a comment\n"
               b"    s = 'helper_func inside a string'\n"
               b"    e = Engine()\n"
               b"    e.start_engine()\n"
               b"    return helper_func()\n")
        counts = extract.extract_refs("python", src)
        self.assertEqual(counts["helper_func"], 1)
        self.assertEqual(counts["Engine"], 1)
        self.assertEqual(counts["start_engine"], 1)
        self.assertNotIn("request", counts)
        self.assertNotIn("prepare_arg", counts)

    def test_refs_fallback_language(self):
        # No ref query for this language -> lexical fallback still counts.
        counts = extract.extract_refs("lua", b"local x = compute_stuff(1)")
        self.assertIn("compute_stuff", counts)


class TestExtractCSharp(unittest.TestCase):
    """Regression fixtures for the Unity battleground: C# must stay precise."""

    SRC = (b"public class GameManager : MonoBehaviour {\n"
           b"    [SerializeField] private HealthBar healthBar;\n"
           b"    public int Score { get; set; }\n"
           b"    public GameManager(Config config) { }\n"
           b"    void Start() {\n"
           b"        var spawner = new EnemySpawner();\n"
           b"        spawner.SpawnWave(3);\n"
           b"        Utils.Log(\"EnemySpawner inside a string\");\n"
           b"    }\n"
           b"}\n"
           b"public interface IDamageable { }\n"
           b"public enum GamePhase { Menu, Play }\n")

    def test_symbols(self):
        syms = {(s.name, s.kind, s.line) for s in
                extract.extract_symbols("csharp", self.SRC)}
        self.assertIn(("GameManager", "class", 1), syms)      # class
        self.assertIn(("Score", "method", 3), syms)           # property
        self.assertIn(("GameManager", "method", 4), syms)     # constructor
        self.assertIn(("Start", "method", 5), syms)
        self.assertIn(("IDamageable", "class", 11), syms)
        self.assertIn(("GamePhase", "type", 12), syms)

    def test_refs_precise(self):
        counts = extract.extract_refs("csharp", self.SRC)
        self.assertEqual(counts["MonoBehaviour"], 1)   # inheritance
        self.assertEqual(counts["SerializeField"], 1)  # attribute
        self.assertEqual(counts["EnemySpawner"], 1)    # `new`, not the string
        self.assertEqual(counts["SpawnWave"], 1)       # method call
        self.assertEqual(counts["HealthBar"], 1)       # field type
        self.assertEqual(counts["Config"], 1)          # parameter type
        self.assertNotIn("healthBar", counts)          # field name is not a ref
        self.assertNotIn("spawner", counts)            # local var is not a ref


class TestCSharpDeclarations(unittest.TestCase):
    SRC = (b"[TestFixture]\n"
           b"public class Suite {\n"
           b"    [SerializeField] private HealthBar healthBar;\n"
           b"    public const int MaxHp = 10, MinHp = 1;\n"
           b"    public event Action<int> Changed;\n"
           b"    public event EventHandler Clicked { add {} remove {} }\n"
           b"    public delegate void Done(int code);\n"
           b"    public record Point(int X, int Y);\n"
           b"    /// <summary>doc</summary>\n"
           b"    [Test]\n"
           b"    [Category(\"slow\")]\n"
           b"    public void Runs() {}\n"
           b"    public WindowData     Data      { get; }\n"
           b"}\n")

    def by_name(self):
        return {s.name: s for s in extract.extract_symbols("csharp", self.SRC)}

    def test_signature_skips_attribute_lines(self):
        syms = self.by_name()
        # The review found 19% of C# signatures were just `[Test]`.
        self.assertEqual(syms["Suite"].signature, "public class Suite {")
        self.assertEqual(syms["Suite"].line, 2)
        self.assertEqual(syms["Runs"].signature, "public void Runs() {}")
        self.assertEqual(syms["Runs"].line, 12)
        self.assertEqual(syms["Suite"].end_line, 14)

    def test_same_line_attribute_kept(self):
        self.assertEqual(self.by_name()["healthBar"].signature,
                         "[SerializeField] private HealthBar healthBar;")

    def test_alignment_whitespace_squeezed(self):
        self.assertEqual(self.by_name()["Data"].signature,
                         "public WindowData Data { get; }")

    def test_members_delegates_records(self):
        syms = self.by_name()
        self.assertEqual(syms["healthBar"].kind, "field")
        self.assertEqual(syms["MinHp"].kind, "field")   # multi-declarator
        self.assertEqual(syms["MinHp"].signature,
                         "public const int MaxHp = 10, MinHp = 1;")
        self.assertEqual(syms["Changed"].kind, "event")
        self.assertEqual(syms["Clicked"].kind, "event")
        self.assertEqual(syms["Done"].kind, "type")     # delegate
        self.assertEqual(syms["Point"].kind, "class")   # record

    def test_java_annotations_skipped(self):
        src = (b"@Service\n"
               b"public class Svc {\n"
               b"    @Override\n"
               b"    public String toString() { return \"\"; }\n"
               b"}\n")
        syms = {s.name: s for s in extract.extract_symbols("java", src)}
        self.assertEqual(syms["Svc"].signature, "public class Svc {")
        self.assertEqual(syms["toString"].line, 4)


class TestCSharpRefConstructs(unittest.TestCase):
    """One fixture per C# use-site construct that name-based `refs` must see.
    The review that motivated these found 0 of 221 files for a static class."""

    def refs(self, body: bytes, wrap: bool = True):
        src = (b"class Host {\n    void Run(object o) {\n" + body + b"\n    }\n}\n"
               if wrap else body)
        counts, lines = extract.extract_refs_with_lines("csharp", src)
        return counts, lines

    def test_static_class_receiver(self):
        counts, _ = self.refs(b'ExLog.Info("ExLog in a string"); // ExLog')
        self.assertEqual(counts["ExLog"], 1)  # receiver, not string/comment
        self.assertEqual(counts["Info"], 1)

    def test_enum_value_and_constant(self):
        counts, _ = self.refs(b"var m = WindowDisplayMode.Popup;\n"
                              b"int k = Limits.MaxCount;")
        self.assertEqual(counts["WindowDisplayMode"], 1)
        self.assertEqual(counts["Popup"], 1)
        self.assertEqual(counts["Limits"], 1)
        self.assertEqual(counts["MaxCount"], 1)

    def test_nested_member_chain(self):
        counts, _ = self.refs(b"Services.Audio.Play(Clips.Click);")
        for name in ("Services", "Audio", "Play", "Clips", "Click"):
            self.assertEqual(counts[name], 1, name)

    def test_generic_arguments(self):
        counts, _ = self.refs(b"var hb = GetComponent<HealthBar>();\n"
                              b"var map = new Dictionary<string, List<EnemyUnit>>();")
        self.assertEqual(counts["HealthBar"], 1)
        self.assertEqual(counts["EnemyUnit"], 1)
        self.assertEqual(counts["GetComponent"], 1)

    def test_member_types(self):
        src = (b"class Host {\n"
               b"    public WindowsManager Windows { get; set; }\n"
               b"    public event Action<WindowEvent> Changed;\n"
               b"    public SlotView this[int i] => null;\n"
               b"    private readonly PoolConfig _pool;\n"
               b"    public Result Compute(Request request) => null;\n"
               b"}\n")
        counts, _ = self.refs(src, wrap=False)
        for name in ("WindowsManager", "WindowEvent", "SlotView", "PoolConfig",
                     "Result", "Request"):
            self.assertEqual(counts[name], 1, name)
        for decl in ("Windows", "Changed", "_pool", "Compute", "Host"):
            self.assertNotIn(decl, counts)  # declaration names are not uses

    def test_type_tests_and_casts(self):
        counts, _ = self.refs(b"var t = typeof(TypeOfTarget);\n"
                              b"if (o is PatternTarget p) {}\n"
                              b"if (o is IsTarget) {}\n"
                              b"var a = o as AsTarget;\n"
                              b"var c = (CastTarget)o;\n"
                              b"var n = nameof(NameOfTarget);")
        for name in ("TypeOfTarget", "PatternTarget", "IsTarget", "AsTarget",
                     "CastTarget", "NameOfTarget"):
            self.assertEqual(counts[name], 1, name)

    def test_attributes(self):
        src = (b"[TestFixture, Category(Categories.Slow)]\n"
               b"[ExDocs(typeof(DocTarget))]\n"
               b"class Host { }\n")
        counts, _ = self.refs(src, wrap=False)
        self.assertEqual(counts["TestFixture"], 1)
        self.assertEqual(counts["TestFixtureAttribute"], 1)  # [X] uses XAttribute
        self.assertEqual(counts["ExDocsAttribute"], 1)
        self.assertEqual(counts["Categories"], 1)  # attribute argument
        self.assertEqual(counts["DocTarget"], 1)

    def test_method_group_and_null_conditional(self):
        counts, _ = self.refs(b"button.onClick.AddListener(OnPressed);\n"
                              b"target?.Refresh();")
        self.assertEqual(counts["OnPressed"], 1)  # method group argument
        self.assertEqual(counts["Refresh"], 1)
        self.assertNotIn("button", counts)  # camelCase locals stay out
        self.assertNotIn("target", counts)

    def test_namespaces_and_locals_excluded(self):
        src = (b"using System.Collections;\n"
               b"using static Logging.ExLog;\n"
               b"using Pool = Engine.ObjectPool;\n"
               b"namespace Game.Editor {\n"
               b"class Host { void Run(int Amount) {\n"
               b"    foreach (var Enemy in Enemies) { }\n"
               b"    Spawn(Amount: 3);\n"
               b"} }\n}\n")
        counts, _ = self.refs(src, wrap=False)
        for ns in ("System", "Collections", "Game", "Editor", "Pool"):
            self.assertNotIn(ns, counts)  # namespace names / alias declaration
        self.assertEqual(counts["ExLog"], 1)       # `using static` names a type
        self.assertEqual(counts["ObjectPool"], 1)  # alias target
        self.assertEqual(counts["Enemies"], 1)
        self.assertEqual(counts["Spawn"], 1)
        self.assertNotIn("Enemy", counts)   # foreach variable declaration
        self.assertNotIn("Amount", counts)  # parameter + named-argument label

    def test_lines(self):
        _, lines = self.refs(b"ExLog.Info(1);\nvar x = 2;\nExLog.Warn(ExLog.Tag);")
        self.assertEqual(lines["ExLog"], [3, 5])  # distinct lines, 1-based


class TestExtractTypeScript(unittest.TestCase):
    SRC = (b"import { fetchUser } from './api';\n"
           b"export interface UserProfile { id: number; }\n"
           b"type ProfileMap = Record<string, UserProfile>;\n"
           b"export class ProfileStore {\n"
           b"    private cache: ProfileMap = {};\n"
           b"    async loadProfile(id: number): Promise<UserProfile> {\n"
           b"        return fetchUser(id);\n"
           b"    }\n"
           b"}\n"
           b"const renderCard = (p: UserProfile) => p.id;\n")

    def test_symbols(self):
        syms = {(s.name, s.kind) for s in
                extract.extract_symbols("typescript", self.SRC)}
        self.assertIn(("UserProfile", "type"), syms)   # interface
        self.assertIn(("ProfileMap", "type"), syms)    # type alias
        self.assertIn(("ProfileStore", "class"), syms)
        self.assertIn(("loadProfile", "method"), syms)
        self.assertIn(("renderCard", "function"), syms)  # arrow function const

    def test_refs(self):
        counts = extract.extract_refs("typescript", self.SRC)
        self.assertGreaterEqual(counts["fetchUser"], 2)   # import + call
        self.assertGreaterEqual(counts["UserProfile"], 2)  # type usages
        self.assertNotIn("cache", counts)  # field name is not a ref


class TestExtractGo(unittest.TestCase):
    SRC = (b"package engine\n"
           b"type Engine struct{ fuel int }\n"
           b"type Starter interface{ Start() }\n"
           b"func NewEngine() *Engine { return &Engine{} }\n"
           b"func (e *Engine) Start() {\n"
           b"    prepareFuel(e)\n"
           b"}\n"
           b"func prepareFuel(e *Engine) {}\n")

    def test_symbols(self):
        syms = {(s.name, s.kind) for s in extract.extract_symbols("go", self.SRC)}
        self.assertIn(("Engine", "type"), syms)
        self.assertIn(("Starter", "type"), syms)
        self.assertIn(("NewEngine", "function"), syms)
        self.assertIn(("Start", "method"), syms)
        self.assertIn(("prepareFuel", "function"), syms)

    def test_refs(self):
        counts = extract.extract_refs("go", self.SRC)
        self.assertIn("prepareFuel", counts)   # call
        self.assertIn("Engine", counts)        # type usages
        self.assertNotIn("fuel", counts)       # struct field is not a ref


if __name__ == "__main__":
    unittest.main()
