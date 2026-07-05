import unittest
from collections import Counter

from lotsman import graph


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


if __name__ == "__main__":
    unittest.main()
