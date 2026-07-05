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
