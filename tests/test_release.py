"""Release consistency: version metadata must tell one story everywhere.

Prevents the exact failure class a past audit hit: CHANGELOG claiming one
version while packaging metadata claims another.
"""

import re
import unittest
from pathlib import Path

import lotsman

REPO_ROOT = Path(__file__).resolve().parent.parent


def _pyproject_version() -> str:
    # Regex instead of tomllib: tomllib is 3.11+, CI runs 3.10 too.
    text = (REPO_ROOT / "pyproject.toml").read_text()
    m = re.search(r'^version\s*=\s*"([^"]+)"', text, re.M)
    assert m, "version not found in pyproject.toml"
    return m.group(1)


def _changelog_top_version() -> str:
    text = (REPO_ROOT / "CHANGELOG.md").read_text()
    m = re.search(r"^## (\d+\.\d+\.\d+)", text, re.M)
    assert m, "no version heading in CHANGELOG.md"
    return m.group(1)


class TestReleaseConsistency(unittest.TestCase):
    def test_versions_match(self):
        self.assertEqual(_pyproject_version(), lotsman.__version__,
                         "pyproject.toml and lotsman.__version__ diverge")
        self.assertEqual(_changelog_top_version(), lotsman.__version__,
                         "CHANGELOG top entry and lotsman.__version__ diverge")

    def test_beta_classifier_still_honest(self):
        # Raising maturity is a deliberate decision, not a side effect:
        # this test forces whoever bumps it to also delete this line.
        text = (REPO_ROOT / "pyproject.toml").read_text()
        self.assertIn("Development Status :: 4 - Beta", text)


if __name__ == "__main__":
    unittest.main()
