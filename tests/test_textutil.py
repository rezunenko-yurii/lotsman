import unittest

from lotsman import textutil


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


if __name__ == "__main__":
    unittest.main()
