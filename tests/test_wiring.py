import unittest

from helpers import FixtureRepoMixin
from lotsman import indexer, wiring


class TestWiring(FixtureRepoMixin, unittest.TestCase):
    def _write_config(self, body: str) -> None:
        d = self.tmp / ".lotsman"
        d.mkdir(exist_ok=True)
        (d / "wiring.json").write_text(body)

    def test_pattern_adds_ident(self):
        self._write_config('{"patterns": [{"regex": "Bind<(\\\\w+)>"}]}')
        (self.tmp / "pkg" / "di.py").write_text('WIRE = "Bind<FuelPump>()"\n')
        indexer.index_repo(self.tmp, self.store)
        paths = [p for p, _ in self.store.files_referencing("FuelPump")]
        self.assertIn("pkg/di.py", paths)

    def test_bad_regex_reported_not_fatal(self):
        patterns, errors = wiring.load_from_text('{"patterns": [{"regex": "("}]}')
        self.assertEqual(patterns, [])
        self.assertTrue(errors)

    def test_wrong_shape_root_reported_not_fatal(self):
        patterns, errors = wiring.load_from_text("[]")
        self.assertEqual(patterns, [])
        self.assertTrue(errors)

    def test_wrong_shape_pattern_entry_reported_not_fatal(self):
        patterns, errors = wiring.load_from_text('{"patterns": [1]}')
        self.assertEqual(patterns, [])
        self.assertTrue(errors)

    def test_config_change_triggers_full_rebuild(self):
        indexer.index_repo(self.tmp, self.store)
        self._write_config('{"patterns": [{"regex": "Bind<(\\\\w+)>"}]}')
        (self.tmp / "pkg" / "di.py").write_text('WIRE = "Bind<FuelPump>()"\n')
        indexer.index_repo(self.tmp, self.store)
        self.assertTrue(self.store.get_meta("wiring_sha"))


if __name__ == "__main__":
    unittest.main()
