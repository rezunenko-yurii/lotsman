import shutil
import tempfile
import unittest
from pathlib import Path

from lotsman import indexer, search
from lotsman.outlineview import outline_rows
from lotsman.store import SymbolRow


def row(name, kind, line, sig=""):
    return SymbolRow("a.cs", name, kind, line, line, sig or f"{kind} {name}")


class TestOutlineRows(unittest.TestCase):
    def test_field_runs_collapse_single_field_keeps_signature(self):
        rows = [row("Dto", "class", 1, "class Dto {"),
                row("Id", "field", 2), row("Name", "field", 3),
                row("Tags", "field", 4),
                row("Validate", "method", 6, "bool Validate()"),
                row("_cache", "field", 8, "private Cache _cache;"),
                row("Changed", "event", 9), row("Closed", "event", 10)]
        out = outline_rows(rows)
        self.assertEqual(out, [
            "    1-1     [class] class Dto {",
            "    2-4     [fields] Id, Name, Tags",
            "    6-6     [method] bool Validate()",
            "    8-8     [field] private Cache _cache;",
            "    9-10    [events] Changed, Closed",
        ])


class TestMemberKindsStayOutOfRanking(unittest.TestCase):
    """Fields/events show in outline and defs, not in search or the map."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="lotsman_test_"))
        (self.tmp / "Mixer.cs").write_text(
            "public class AudioMixerService {\n"
            "    private AudioMixer _mixerVolume;\n"
            "    public event Action MixerVolumeChanged;\n"
            "    public void SetMixerVolume(float v) { }\n"
            "}\n")
        self.store = indexer.open_store(self.tmp)
        indexer.index_repo(self.tmp, self.store)

    def tearDown(self):
        self.store.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_members_listed_but_not_searched(self):
        kinds = {r.name: r.kind for r in self.store.symbols_in_file("Mixer.cs")}
        self.assertEqual(kinds["_mixerVolume"], "field")
        self.assertEqual(kinds["MixerVolumeChanged"], "event")
        self.assertEqual(len(self.store.symbols_named("_mixerVolume")), 1)
        hits = search.search(self.store, "mixer volume", mode="bm25")
        self.assertTrue(hits)
        self.assertTrue(all(h.symbol.kind not in ("field", "event") for h in hits))
        self.assertNotIn("_mixerVolume", self.store.definitions())


if __name__ == "__main__":
    unittest.main()
