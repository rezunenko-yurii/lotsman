import shutil
import tempfile
import unittest
from collections import Counter
from pathlib import Path

from lotsman import graph, indexer


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

    def test_foreign_member_edges_dropped(self):
        # `dict.TryGetValue(...)` in b.cs is not a use of Registry.TryGetValue:
        # b.cs never mentions `Registry`. a.cs does, so its edge stays.
        definitions = {"TryGetValue": {"Registry.cs"}, "Registry": {"Registry.cs"}}
        references = {
            "a.cs": Counter({"Registry": 1, "TryGetValue": 1}),
            "b.cs": Counter({"TryGetValue": 4}),
        }
        owners = {("TryGetValue", "Registry.cs"): {"Registry"}}
        edges = graph.build_edges(definitions, references, owners=owners)
        self.assertIn("Registry.cs", edges["a.cs"])
        self.assertNotIn("b.cs", edges)

    def test_generic_names_form_no_edges_unless_mentioned(self):
        definitions = {"Window": {"Window.cs"}}
        references = {"a.cs": Counter({"Window": 2})}
        self.assertEqual(dict(graph.build_edges(definitions, references)), {})
        edges = graph.build_edges(definitions, references, mentions={"Window"})
        self.assertIn("Window.cs", edges["a.cs"])


class TestMemberOwners(unittest.TestCase):
    def test_owner_is_innermost_enclosing_type(self):
        tmp = Path(tempfile.mkdtemp(prefix="lotsman_test_"))
        try:
            (tmp / "Reg.cs").write_text(
                "public class Registry {\n"
                "    public bool TryGetValue(string k) => true;\n"
                "    class Inner {\n"
                "        void Flush() { }\n"
                "    }\n"
                "}\n"
                "public static class Tools { public static void Flush() { } }\n")
            store = indexer.open_store(tmp)
            indexer.index_repo(tmp, store)
            owners = store.member_owners((".cs",))
            store.close()
            self.assertEqual(owners[("TryGetValue", "Reg.cs")], {"Registry"})
            self.assertEqual(owners[("Flush", "Reg.cs")], {"Inner", "Tools"})
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
