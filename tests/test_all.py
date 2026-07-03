"""Unit and end-to-end tests for codemap (stdlib unittest, no extra deps)."""

from __future__ import annotations

import shutil
import tempfile
import unittest
from collections import Counter
from pathlib import Path

from codemap import embed, extract, graph, indexer, repomap, search, textutil
from codemap.search import Hit
from codemap.store import Store, SymbolRow


class TestTextUtil(unittest.TestCase):
    def test_split_ident(self):
        self.assertEqual(textutil.split_ident("fooBarBaz"), ["foo", "bar", "baz"])
        self.assertEqual(textutil.split_ident("HTTPServer"), ["http", "server"])
        self.assertEqual(textutil.split_ident("snake_case_name"),
                         ["snake", "case", "name"])
        self.assertEqual(textutil.split_ident("v2Parser"), ["v2", "parser"])

    def test_tokenize_skips_stopwords(self):
        tokens = textutil.tokenize("def compute_total(items): return sum")
        self.assertIn("compute_total", tokens)
        self.assertIn("compute", tokens)
        self.assertIn("total", tokens)
        self.assertNotIn("def", tokens)
        self.assertNotIn("return", tokens)

    def test_well_named(self):
        self.assertTrue(textutil.is_well_named("compute_totals"))
        self.assertTrue(textutil.is_well_named("computeTotals"))
        self.assertFalse(textutil.is_well_named("main"))
        self.assertFalse(textutil.is_well_named("CONSTANT"))


class TestExtract(unittest.TestCase):
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


class TestGraph(unittest.TestCase):
    def test_pagerank_favors_referenced_file(self):
        definitions = {"core_helper": {"core.py"}, "util_thing": {"util.py"}}
        references = {
            "a.py": Counter({"core_helper": 3}),
            "b.py": Counter({"core_helper": 2}),
            "c.py": Counter({"util_thing": 1}),
        }
        edges = graph.build_edges(definitions, references)
        nodes = {"a.py", "b.py", "c.py", "core.py", "util.py"}
        rank = graph.pagerank(nodes, edges)
        self.assertGreater(rank["core.py"], rank["util.py"])
        def_ranks = graph.rank_definitions(rank, edges)
        self.assertGreater(def_ranks[("core.py", "core_helper")],
                           def_ranks[("util.py", "util_thing")])

    def test_mentions_boost(self):
        definitions = {"aaa_func": {"a.py"}, "bbb_func": {"b.py"}}
        references = {
            "x.py": Counter({"aaa_func": 1, "bbb_func": 1}),
        }
        edges = graph.build_edges(definitions, references, mentions={"bbb_func"})
        rank = graph.pagerank({"a.py", "b.py", "x.py"}, edges)
        self.assertGreater(rank["b.py"], rank["a.py"])

    def test_self_reference_excluded(self):
        definitions = {"solo": {"a.py"}}
        references = {"a.py": Counter({"solo": 5})}
        edges = graph.build_edges(definitions, references)
        self.assertEqual(dict(edges), {})


class FixtureRepoMixin:
    """Creates a small multi-file Python project for e2e tests."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="codemap_test_"))
        (self.tmp / "pkg").mkdir()
        (self.tmp / "pkg" / "core.py").write_text(
            "class Engine:\n"
            "    def start_engine(self):\n"
            "        return prepare_fuel()\n"
            "\n"
            "def prepare_fuel():\n"
            "    return 42\n")
        (self.tmp / "pkg" / "app.py").write_text(
            "from pkg.core import Engine, prepare_fuel\n"
            "\n"
            "def run_application():\n"
            "    e = Engine()\n"
            "    e.start_engine()\n"
            "    prepare_fuel()\n"
            "    prepare_fuel()\n")
        (self.tmp / "main.py").write_text(
            "from pkg.app import run_application\n"
            "run_application()\n")
        self.store = indexer.open_store(self.tmp)
        indexer.index_repo(self.tmp, self.store)

    def tearDown(self):
        self.store.close()
        shutil.rmtree(self.tmp, ignore_errors=True)


class TestEndToEnd(FixtureRepoMixin, unittest.TestCase):
    def test_index_contents(self):
        stats = self.store.stats()
        self.assertEqual(stats["files"], 3)
        names = {s.name for s in self.store.all_symbols()}
        self.assertEqual(
            names, {"Engine", "start_engine", "prepare_fuel", "run_application"})

    def test_incremental_noop(self):
        res = indexer.index_repo(self.tmp, self.store)
        self.assertEqual(res.added + res.updated + res.removed, 0)
        self.assertEqual(res.unchanged, 3)

    def test_incremental_change_and_delete(self):
        (self.tmp / "pkg" / "core.py").write_text("def new_func():\n    pass\n")
        (self.tmp / "main.py").unlink()
        res = indexer.index_repo(self.tmp, self.store)
        self.assertEqual(res.updated, 1)
        self.assertEqual(res.removed, 1)
        names = {s.name for s in self.store.all_symbols()}
        self.assertIn("new_func", names)
        self.assertNotIn("Engine", names)

    def test_repo_map_ranks_core(self):
        out = repomap.generate_map(self.store, budget=512)
        self.assertIn("pkg/core.py:", out)
        self.assertIn("prepare_fuel", out)
        # core.py (most referenced) must be listed before app.py
        self.assertLess(out.index("pkg/core.py"), out.index("pkg/app.py"))

    def test_repo_map_focus_excluded(self):
        out = repomap.generate_map(self.store, budget=512,
                                   focus={"pkg/core.py"})
        self.assertNotIn("pkg/core.py:", out)

    def test_repo_map_budget(self):
        out = repomap.generate_map(self.store, budget=48)
        self.assertLessEqual(textutil.estimate_tokens(out), 64)

    def test_search_finds_symbol(self):
        hits = search.search(self.store, "prepare fuel")
        self.assertTrue(hits)
        self.assertEqual(hits[0].symbol.name, "prepare_fuel")
        self.assertEqual(hits[0].symbol.path, "pkg/core.py")

    def test_search_subtoken_match(self):
        hits = search.search(self.store, "application")
        self.assertTrue(hits)
        self.assertEqual(hits[0].symbol.name, "run_application")

    def test_refs_lookup(self):
        refs = dict(self.store.files_referencing("prepare_fuel"))
        self.assertIn("pkg/app.py", refs)
        self.assertEqual(refs["pkg/app.py"], 3)  # import + 2 calls


class TestSearchModes(FixtureRepoMixin, unittest.TestCase):
    def _mk_hit(self, path: str, line: int, score: float) -> Hit:
        return Hit(score, SymbolRow(path, "x", "function", line, line, "def x():"))

    def test_rrf_fusion(self):
        # doc B is rank 2 in both lists and must beat docs that are rank 1 in one
        # list but absent from the other.
        list1 = [self._mk_hit("a.py", 1, 9.0), self._mk_hit("b.py", 2, 8.0)]
        list2 = [self._mk_hit("c.py", 3, 0.9), self._mk_hit("b.py", 2, 0.8)]
        fused = search._rrf([list1, list2], limit=3)
        self.assertEqual(fused[0].symbol.path, "b.py")

    def test_bm25_mode_without_vectors(self):
        hits = search.search(self.store, "prepare fuel", mode="bm25")
        self.assertEqual(hits[0].symbol.name, "prepare_fuel")

    def test_auto_mode_degrades_without_vectors(self):
        # Fixture store has no vectors -> auto must behave like bm25, not crash.
        hits = search.search(self.store, "engine", mode="auto")
        self.assertTrue(hits)

    @unittest.skipUnless(embed.available(), "model2vec model not available")
    def test_hybrid_and_vector_modes(self):
        n = embed.embed_missing(self.store)
        self.assertGreater(n, 0)
        self.assertEqual(self.store.vector_count(), n)
        vec_hits = search.search(self.store, "start the motor", mode="vector")
        self.assertTrue(vec_hits)
        hy = search.search(self.store, "prepare fuel", mode="hybrid")
        self.assertEqual(hy[0].symbol.name, "prepare_fuel")
        # re-embed is a no-op (incremental)
        self.assertEqual(embed.embed_missing(self.store), 0)

    @unittest.skipUnless(embed.available(), "model2vec model not available")
    def test_changed_file_reembedded(self):
        embed.embed_missing(self.store)
        (self.tmp / "pkg" / "core.py").write_text("def fresh_symbol():\n    pass\n")
        indexer.index_repo(self.tmp, self.store)
        missing = self.store.conn.execute(
            "SELECT COUNT(*) FROM symbols WHERE vector IS NULL").fetchone()[0]
        self.assertEqual(missing, 1)  # only the rewritten file's symbol
        self.assertEqual(embed.embed_missing(self.store), 1)


class TestCLI(FixtureRepoMixin, unittest.TestCase):
    def test_cli_commands_run(self):
        from codemap.cli import main
        import contextlib, io
        for argv in (
            ["--repo", str(self.tmp), "index"],
            ["--repo", str(self.tmp), "map", "--budget", "256"],
            ["--repo", str(self.tmp), "search", "engine", "--json"],
            ["--repo", str(self.tmp), "outline", "pkg/core.py"],
            ["--repo", str(self.tmp), "defs", "Engine"],
            ["--repo", str(self.tmp), "refs", "prepare_fuel"],
            ["--repo", str(self.tmp), "stats"],
        ):
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(io.StringIO()):
                code = main(argv)
            self.assertEqual(code, 0, f"{argv} -> {buf.getvalue()}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
