"""A grammar that fails to load must not degrade results silently."""

import contextlib
import io
import unittest
from unittest import mock

from helpers import FixtureRepoMixin
from lotsman import extract, indexer
from lotsman.cli import main
from lotsman.mcp_server import McpServer

_real_compile = extract._compile


def _python_broken(lang, which):
    if lang == "python":
        extract._COMPILE_ERRORS[(lang, which)] = "ImportError: simulated"
        return None
    return _real_compile(lang, which)


@contextlib.contextmanager
def broken_python_grammar():
    # A grammar breaks when the environment changes (package upgrade, new
    # Python) — which is what moves the fingerprint the health check keys on.
    with mock.patch.object(extract, "_compile", _python_broken), \
            mock.patch.object(extract, "grammar_fingerprint",
                              lambda langs: "broken-env"):
        yield


class TestDegradedGrammar(FixtureRepoMixin, unittest.TestCase):
    def test_failures_reported_with_reason(self):
        with mock.patch.object(extract, "_compile", _python_broken):
            failures = extract.grammar_failures(["python", "lua", "csharp"])
        # lua has no queries (lexical by design) — not a failure
        self.assertEqual(failures, {"python": "ImportError: simulated"})

    def test_cli_warns_and_index_rebuilds_on_recovery(self):
        err = io.StringIO()
        with broken_python_grammar(), \
                contextlib.redirect_stdout(io.StringIO()), \
                contextlib.redirect_stderr(err):
            main(["--repo", str(self.tmp), "outline", "pkg/core.py"])
        self.assertIn("tree-sitter unavailable for python", err.getvalue())
        self.assertIn("ImportError: simulated", err.getvalue())
        # Still broken on the next run: warned again from the cached check.
        with broken_python_grammar(), mock.patch.object(
                extract, "grammar_failures", side_effect=AssertionError):
            res = indexer.index_repo(self.tmp, self.store)
        self.assertIn("python", res.degraded)
        # Grammar back: the fallback-built rows are replaced, not kept.
        res = indexer.index_repo(self.tmp, self.store)
        self.assertEqual(res.degraded, {})
        self.assertEqual(res.added, 3)

    def test_mcp_output_carries_warning(self):
        server = McpServer(self.tmp)
        server.store.close()
        server.store = self.store
        with broken_python_grammar():
            reply = server.handle({
                "jsonrpc": "2.0", "id": 1, "method": "tools/call",
                "params": {"name": "refs", "arguments": {"name": "prepare_fuel"}}})
        text = reply["result"]["content"][0]["text"]
        self.assertIn("tree-sitter unavailable for python", text)


if __name__ == "__main__":
    unittest.main()
