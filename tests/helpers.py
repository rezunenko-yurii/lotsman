"""Shared test fixtures."""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

from lotsman import indexer


class FixtureRepoMixin:
    """Creates a small multi-file Python project for e2e tests."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="lotsman_test_"))
        (self.tmp / "pkg").mkdir()
        (self.tmp / "pkg" / "core.py").write_text(
            "class Engine:\n"
            "    def start_engine(self):\n"
            "        return prepare_fuel()\n"
            "\n"
            "def prepare_fuel():\n"
            "    return 42\n")
        (self.tmp / "pkg" / "app.py").write_text(
            "from pkg.core import Engine, prepare_fuel\n"
            "\n"
            "def run_application():\n"
            "    e = Engine()\n"
            "    e.start_engine()\n"
            "    prepare_fuel()\n"
            "    prepare_fuel()\n")
        (self.tmp / "main.py").write_text(
            "from pkg.app import run_application\n"
            "run_application()\n")
        self.store = indexer.open_store(self.tmp)
        indexer.index_repo(self.tmp, self.store)

    def tearDown(self):
        self.store.close()
        shutil.rmtree(self.tmp, ignore_errors=True)
