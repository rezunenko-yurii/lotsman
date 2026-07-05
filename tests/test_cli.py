import contextlib
import io
import unittest

from helpers import FixtureRepoMixin
from lotsman.cli import main


class TestCLI(FixtureRepoMixin, unittest.TestCase):
    def test_cli_commands_run(self):
        for argv in (
            ["--repo", str(self.tmp), "index"],
            ["--repo", str(self.tmp), "index", "--verify", "--no-embed"],
            ["--repo", str(self.tmp), "map", "--budget", "256"],
            ["--repo", str(self.tmp), "search", "engine", "--json"],
            ["--repo", str(self.tmp), "outline", "pkg/core.py"],
            ["--repo", str(self.tmp), "defs", "Engine"],
            ["--repo", str(self.tmp), "refs", "prepare_fuel"],
            ["--repo", str(self.tmp), "impact", "pkg/core.py"],
            ["--repo", str(self.tmp), "stats"],
            ["--repo", str(self.tmp), "doctor", "--json"],
        ):
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf), \
                    contextlib.redirect_stderr(io.StringIO()):
                code = main(argv)
            self.assertEqual(code, 0, f"{argv} -> {buf.getvalue()}")


if __name__ == "__main__":
    unittest.main()
