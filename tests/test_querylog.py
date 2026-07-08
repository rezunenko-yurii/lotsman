import io
import json
import os
import unittest
from contextlib import redirect_stdout
from unittest import mock

from helpers import FixtureRepoMixin
from lotsman import cli
import lotsman.querylog as querylog


class TestQueryLog(FixtureRepoMixin, unittest.TestCase):
    def test_disabled_by_default_writes_nothing(self):
        querylog.log(self.tmp, "search", {"query": "x"}, "(no results)")
        self.assertFalse((self.tmp / ".lotsman" / "querylog.jsonl").exists())

    def test_enabled_appends_and_flags_empty(self):
        with mock.patch.dict(os.environ, {"LOTSMAN_QUERYLOG": "1"}):
            querylog.log(self.tmp, "search", {"query": "x"}, "(no results)")
            querylog.log(self.tmp, "refs", {"name": "Tank"},
                         "defined in:\n  a.py:1")
        rows = [json.loads(line) for line in
                (self.tmp / ".lotsman" / "querylog.jsonl")
                .read_text(encoding="utf-8").splitlines()]
        self.assertEqual([row["empty"] for row in rows], [True, False])

    def test_summarize(self):
        with mock.patch.dict(os.environ, {"LOTSMAN_QUERYLOG": "1"}):
            querylog.log(self.tmp, "search", {"query": "fuel"}, "(no results)")
        out = querylog.summarize(self.tmp)
        self.assertIn("search", out)
        self.assertIn("empty", out)

    def test_report_command_prints_summary(self):
        with mock.patch.dict(os.environ, {"LOTSMAN_QUERYLOG": "1"}):
            querylog.log(self.tmp, "search", {"query": "fuel"}, "(no results)")
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = cli.main(["--repo", str(self.tmp), "report"])
        self.assertEqual(rc, 0)
        self.assertIn("query log summary:", buf.getvalue())


if __name__ == "__main__":
    unittest.main()
