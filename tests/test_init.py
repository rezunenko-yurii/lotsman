import contextlib
import io
import json
import unittest

from helpers import FixtureRepoMixin
from lotsman.init_cmd import MARK_BEGIN, MARK_END, run_init


class TestInit(FixtureRepoMixin, unittest.TestCase):
    def _init(self, agents=(), no_index=True) -> str:
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = run_init(self.tmp, agents=list(agents), no_index=no_index)
        self.assertEqual(code, 0)
        return buf.getvalue()

    def test_universal_artifacts(self):
        self._init()
        agents = (self.tmp / "AGENTS.md").read_text()
        self.assertIn(MARK_BEGIN, agents)
        self.assertIn("Code navigation: lotsman", agents)
        self.assertIn("candidates to check, not proof", agents)
        self.assertTrue((self.tmp / ".lotsmanignore").exists())

    def test_idempotent(self):
        self._init()
        first = (self.tmp / "AGENTS.md").read_text()
        self._init()
        second = (self.tmp / "AGENTS.md").read_text()
        self.assertEqual(first, second)
        self.assertEqual(second.count(MARK_BEGIN), 1)

    def test_appends_to_existing_agents_md(self):
        (self.tmp / "AGENTS.md").write_text("# My project\n\nCustom rules.\n")
        self._init()
        text = (self.tmp / "AGENTS.md").read_text()
        self.assertIn("Custom rules.", text)  # preserved
        self.assertIn(MARK_BEGIN, text)       # policy added
        # re-run refreshes only the marked block
        self._init()
        self.assertEqual((self.tmp / "AGENTS.md").read_text().count(MARK_END), 1)

    def test_claude_agent_config(self):
        self._init(agents=["claude"])
        mcp = json.loads((self.tmp / ".mcp.json").read_text())
        self.assertIn("lotsman", mcp["mcpServers"])
        self.assertIn("mcp", mcp["mcpServers"]["lotsman"]["args"])
        claude = (self.tmp / "CLAUDE.md").read_text()
        self.assertIn("@AGENTS.md", claude)

    def test_mcp_json_merge_preserves_other_servers(self):
        (self.tmp / ".mcp.json").write_text(json.dumps(
            {"mcpServers": {"other": {"command": "x"}}}))
        self._init(agents=["claude"])
        mcp = json.loads((self.tmp / ".mcp.json").read_text())
        self.assertIn("other", mcp["mcpServers"])
        self.assertIn("lotsman", mcp["mcpServers"])

    def test_unparseable_mcp_json_left_alone(self):
        (self.tmp / ".mcp.json").write_text("{broken json")
        self._init(agents=["claude"])
        self.assertEqual((self.tmp / ".mcp.json").read_text(), "{broken json")

    def test_gitignore_appended_only_for_git_repos(self):
        self._init()
        self.assertFalse((self.tmp / ".gitignore").exists())  # not a git repo
        (self.tmp / ".git").mkdir()
        self._init()
        self.assertIn(".lotsman/", (self.tmp / ".gitignore").read_text())
        self._init()  # no duplicate line
        self.assertEqual(
            (self.tmp / ".gitignore").read_text().count(".lotsman/"), 1)

    def test_codex_prints_global_registration(self):
        out = self._init(agents=["codex"])
        self.assertIn("codex mcp add lotsman", out)

    def test_cursor_config(self):
        self._init(agents=["cursor"])
        mcp = json.loads((self.tmp / ".cursor" / "mcp.json").read_text())
        self.assertIn("lotsman", mcp["mcpServers"])

    def test_init_with_index_warms_cache(self):
        self._init(no_index=False)
        # reopen the store the same way commands do
        from lotsman import indexer
        store = indexer.open_store(self.tmp)
        try:
            self.assertIsNotNone(
                store.load_rank_cache(store.state_stamp()))
        finally:
            store.close()


if __name__ == "__main__":
    unittest.main()
