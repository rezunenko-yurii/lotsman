import datetime as dt
import io
import json
import os
import time
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

    def test_flag_file_logs_cli_calls(self):
        # Agents often call the CLI from a shell that never sees the MCP
        # config's env — a flag file under the index root turns logging on.
        (self.tmp / ".lotsman" / "querylog.on").touch()
        buf = io.StringIO()
        with redirect_stdout(buf), mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("LOTSMAN_QUERYLOG", None)
            cli.main(["--repo", str(self.tmp), "refs", "prepare_fuel"])
        self.assertIn("pkg/app.py", buf.getvalue())  # output still printed
        rows = [json.loads(line) for line in
                (self.tmp / ".lotsman" / "querylog.jsonl")
                .read_text(encoding="utf-8").splitlines()]
        self.assertEqual(rows[-1]["cmd"], "refs")
        self.assertEqual(rows[-1]["via"], "cli")
        self.assertEqual(rows[-1]["args"]["name"], "prepare_fuel")
        self.assertIn("cli 1", querylog.summarize(self.tmp))

    def test_env_zero_overrides_flag(self):
        (self.tmp / ".lotsman" / "querylog.on").touch()
        with mock.patch.dict(os.environ, {"LOTSMAN_QUERYLOG": "0"}):
            querylog.log(self.tmp, "refs", {"name": "x"}, "out")
        self.assertFalse((self.tmp / ".lotsman" / "querylog.jsonl").exists())

    def test_flag_expires_on_its_date(self):
        # A measurement week must not silently become forever.
        flag = self.tmp / ".lotsman" / "querylog.on"
        flag.write_text("until 2026-09-17\n")
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("LOTSMAN_QUERYLOG", None)
            self.assertTrue(querylog.enabled(self.tmp, today=dt.date(2026, 9, 17)))
            self.assertFalse(querylog.enabled(self.tmp, today=dt.date(2026, 9, 18)))

    def test_dateless_flag_expires_by_default(self):
        flag = self.tmp / ".lotsman" / "querylog.on"
        flag.touch()
        old = time.time() - (querylog.FLAG_DEFAULT_DAYS + 1) * 86400
        os.utime(flag, (old, old))
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("LOTSMAN_QUERYLOG", None)
            self.assertFalse(querylog.enabled(self.tmp))
            querylog.log(self.tmp, "refs", {"name": "x"}, "out")
        self.assertFalse((self.tmp / ".lotsman" / "querylog.jsonl").exists())
        self.assertIn("expired", querylog.summarize(self.tmp))

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
