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

    def test_read_commands_refresh_after_immediate_file_change(self):
        def run_outline() -> str:
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf), \
                    contextlib.redirect_stderr(io.StringIO()):
                code = main(["--repo", str(self.tmp), "outline", "pkg/core.py"])
            self.assertEqual(code, 0)
            return buf.getvalue()

        self.assertIn("start_engine", run_outline())

        (self.tmp / "pkg" / "core.py").write_text(
            "class Engine:\n"
            "    def ignite_engine(self):\n"
            "        return 99\n")

        out = run_outline()
        self.assertIn("ignite_engine", out)
        self.assertNotIn("start_engine", out)


if __name__ == "__main__":
    unittest.main()
