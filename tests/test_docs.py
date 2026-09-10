"""Markdown docs as index entries: sections, Russian search, binding docs."""

import contextlib
import io
import shutil
import tempfile
import unittest
from pathlib import Path

from lotsman import docs, indexer, search
from lotsman.cli import main
from lotsman.mcp_server import McpServer

GUIDE = """---
title: Guide
---

# Таймеры

Обзор модуля.

## Запуск таймера {#start}

Вызовите `Timer.Start()` в компоненте.

```csharp
# not a heading inside code
```

### Пауза

Остановка без сброса.

Известные проблемы
------------------

На просадке кадров таймер отстаёт от часов.
"""


class TestParse(unittest.TestCase):
    def setUp(self):
        self.secs = {s.name: s for s in docs.parse_sections(GUIDE.encode(), "guide.md")}

    def test_headings_ranges_and_anchors(self):
        self.assertEqual(set(self.secs), {"Таймеры", "Запуск таймера", "Пауза",
                                          "Известные проблемы"})
        start = self.secs["Запуск таймера"]
        self.assertEqual((start.line, start.level), (9, 2))
        self.assertEqual(start.signature, "## Запуск таймера {#start}")
        self.assertEqual(start.end_line, 19)  # includes its `### Пауза`
        self.assertEqual(self.secs["Таймеры"].end_line, 24)
        self.assertEqual(self.secs["Известные проблемы"].level, 2)  # setext

    def test_code_and_front_matter_are_not_headings(self):
        self.assertNotIn("not a heading inside code", self.secs)
        self.assertNotIn("title: Guide", self.secs)

    def test_heading_chain_and_own_text_are_terms(self):
        pause = self.secs["Пауза"]
        self.assertIn("таймер", pause.heading_terms)   # parent titles count
        self.assertIn("сброс", pause.body_terms)
        self.assertNotIn("просадк", pause.body_terms)  # next section's text

    def test_file_without_headings_is_one_section(self):
        secs = docs.parse_sections(b"Just text.\nMore.\n", "notes/PLANS.md")
        self.assertEqual([(s.name, s.line, s.end_line) for s in secs],
                         [("PLANS.md", 1, 2)])


class TestTerms(unittest.TestCase):
    def test_russian_forms_share_a_stem(self):
        self.assertEqual({docs.stem_ru(w) for w in ("таймер", "таймера", "таймеру")},
                         {"таймер"})
        self.assertEqual({docs.stem_ru(w) for w in ("кадров", "кадры", "кадр")},
                         {"кадр"})

    def test_terms(self):
        out = docs.terms("Ещё не вызван InjectFields и p_Path")
        self.assertNotIn("еще", out)            # stopwords stay out
        self.assertNotIn("не", out)
        self.assertIn("injectfields", out)
        self.assertIn("inject", out)            # identifier subwords
        self.assertIn(docs.stem_ru("вызван"), out)


class TestBindingAndDocsFor(unittest.TestCase):
    def test_binding_lines_resolve_to_indexed_docs(self):
        known = {"Mod/Docs/DECISIONS.md", "Mod/overview.md", "AGENTS.md"}
        text = ("1. `Mod/Docs/DECISIONS.md` is binding for Mod.\n"
                "2. See `Mod/overview.md` for a tour.\n")
        self.assertEqual(docs.binding_docs("AGENTS.md", text, known),
                         {"Mod/Docs/DECISIONS.md"})
        ru = "Решения в `Docs/DECISIONS.md` обязательны к чтению.\n"
        self.assertEqual(docs.binding_docs("Mod/AGENTS.md", ru, known),
                         {"Mod/Docs/DECISIONS.md"})

    def test_docs_for_picks_nearest_module_most_useful_first(self):
        paths = {"Mod/AGENTS.md", "Mod/CLEANUP_PLAN.md",
                 "Mod/Editor/Documentation/overview.md", "Other/README.md"}
        line = docs.docs_for("Mod/Runtime/Core/Thing.cs", paths)
        self.assertEqual(line, "docs: Mod/{AGENTS.md, CLEANUP_PLAN.md} · "
                               "Editor/Documentation/overview.md")
        self.assertIsNone(docs.docs_for("Lonely/X.cs", {"Else/README.md"}))


class TestDocsInIndex(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="lotsman_docs_"))
        (self.tmp / "Mod" / "Docs").mkdir(parents=True)
        (self.tmp / "Mod" / "guide.md").write_text(GUIDE)
        (self.tmp / "Mod" / "Docs" / "DECISIONS.md").write_text(
            "# Decisions\n\n## D-01 Wildcards Affect Direct Children\n\nOnly direct.\n")
        (self.tmp / "AGENTS.md").write_text(
            "Read `Mod/Docs/DECISIONS.md` — it is binding.\n")
        (self.tmp / "Mod" / "Timer.cs").write_text(
            "public class Timer { public void Start() {} }\n")
        self.store = indexer.open_store(self.tmp)
        indexer.index_repo(self.tmp, self.store)

    def tearDown(self):
        self.store.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def run_cli(self, *argv):
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            main(["--repo", str(self.tmp), *argv])
        return out.getvalue()

    def test_sections_outlined_but_not_code_symbols(self):
        kinds = {r.name: r.kind for r in self.store.symbols_in_file("Mod/guide.md")}
        self.assertEqual(kinds["Пауза"], "section")
        self.assertEqual(self.store.symbols_named("Пауза"), [])       # not a definition
        self.assertNotIn("Пауза", {s.name for s in self.store.all_symbols()})
        self.assertNotIn("Пауза", self.store.definitions())

    def test_search_finds_inflected_russian_with_own_range(self):
        hits = search.search_docs(self.store, "таймеры отстают при просадке кадра",
                                  root=self.tmp)
        self.assertTrue(hits)
        top = hits[0]
        self.assertEqual((top.path, top.line), ("Mod/guide.md", 21))
        self.assertEqual(top.end_line, 24)
        self.assertIn("просадке", top.snippet)
        # a parent section's hit covers its own text only, not the children
        parent = next(h for h in search.search_docs(self.store, "обзор модуля")
                      if h.line == 5)
        self.assertEqual(parent.end_line, 8)

    def test_cli_search_prints_docs_block_after_code(self):
        out = self.run_cli("search", "timer start")
        code, _, block = out.partition("docs:\n")
        self.assertIn("Mod/Timer.cs", code)
        self.assertIn("Mod/guide.md:9-", block)

    def test_binding_marked_in_outline_and_search(self):
        self.assertIn("binding", self.run_cli("outline", "Mod/Docs/DECISIONS.md"))
        self.assertIn("[binding: read in full]", self.run_cli("search", "wildcards direct"))
        self.assertIn("docs: Mod/guide.md", self.run_cli("outline", "Mod/Timer.cs"))

    def test_reindex_replaces_and_deletes_doc_text(self):
        guide = self.tmp / "Mod" / "guide.md"
        guide.write_text("# Таймеры\n\nПереписано про паузу.\n")
        indexer.index_repo(self.tmp, self.store)
        self.assertFalse(search.search_docs(self.store, "просадка кадров"))
        self.assertTrue(search.search_docs(self.store, "переписано"))
        guide.unlink()
        indexer.index_repo(self.tmp, self.store)
        self.assertFalse(search.search_docs(self.store, "переписано"))
        (n,) = self.store.conn.execute("SELECT COUNT(*) FROM docs_fts").fetchone()
        self.assertEqual(n, 3)  # DECISIONS.md (2) + AGENTS.md without headings (1)

    def test_mcp_search_carries_docs(self):
        server = McpServer(self.tmp)
        server.store.close()
        server.store = self.store
        reply = server.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                               "params": {"name": "search",
                                          "arguments": {"query": "пауза без сброса"}}})
        self.assertIn("docs:\n  Mod/guide.md:17", reply["result"]["content"][0]["text"])


if __name__ == "__main__":
    unittest.main()
