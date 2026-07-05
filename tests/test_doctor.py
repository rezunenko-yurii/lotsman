import contextlib
import io
import json
import unittest

from helpers import FixtureRepoMixin
from lotsman.doctor import run_doctor


class TestDoctor(FixtureRepoMixin, unittest.TestCase):
    def _run(self, **kwargs) -> tuple[int, str]:
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = run_doctor(self.tmp, **kwargs)
        return code, buf.getvalue()

    def test_doctor_runs_and_reports(self):
        code, out = self._run()
        self.assertEqual(code, 0)
        self.assertIn("languages", out)
        self.assertIn("python       defs: tree-sitter", out)
        self.assertIn("index is fresh", out)
        self.assertIn("change_detection", out)

    def test_doctor_flags_stale_index(self):
        (self.tmp / "newfile.py").write_text("def brand_new():\n    pass\n")
        code, out = self._run()
        self.assertIn("stale: 1 changed/new", out)
        self.assertEqual(code, 0)  # warn is not a failure by default

    def test_fail_on_warn(self):
        (self.tmp / "newfile.py").write_text("def brand_new():\n    pass\n")
        code, _ = self._run(fail_on_warn=True)
        self.assertEqual(code, 1)

    def test_json_output(self):
        code, out = self._run(as_json=True)
        report = json.loads(out)
        self.assertEqual(code, 0)
        self.assertIn(report["status"], ("ok", "warn"))
        by_name = {c["name"]: c for c in report["checks"]}
        self.assertEqual(by_name["index"]["stale_files"], 0)
        self.assertTrue(by_name["index"]["version_match"])
        self.assertEqual(by_name["languages"]["python"]["defs"], "tree-sitter")
        self.assertEqual(by_name["change_detection"]["method"], "mtime-window")

    def test_json_reflects_staleness(self):
        (self.tmp / "newfile.py").write_text("def brand_new():\n    pass\n")
        _, out = self._run(as_json=True)
        report = json.loads(out)
        self.assertEqual(report["status"], "warn")
        by_name = {c["name"]: c for c in report["checks"]}
        self.assertEqual(by_name["index"]["stale_files"], 1)


if __name__ == "__main__":
    unittest.main()
