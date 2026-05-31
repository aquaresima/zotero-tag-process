"""
test_serve_cli.py — Tests that the `import-tags` and `serve` CLI commands are
registered and expose correct help, without starting uvicorn.
"""

from __future__ import annotations

import pytest
from click.testing import CliRunner

from zotero_restructuring.cli import main


@pytest.fixture
def runner():
    return CliRunner()


class TestImportTagsCommand:
    def test_import_tags_registered(self, runner):
        result = runner.invoke(main, ["import-tags", "--help"])
        assert result.exit_code == 0
        assert "--generated-db" in result.output
        assert "--db" in result.output

    def test_import_tags_in_group(self, runner):
        result = runner.invoke(main, ["--help"])
        assert "import-tags" in result.output

    def test_worker_command_removed(self, runner):
        result = runner.invoke(main, ["worker", "--help"])
        assert result.exit_code != 0


class TestServeCommand:
    def test_serve_registered(self, runner):
        result = runner.invoke(main, ["serve", "--help"])
        assert result.exit_code == 0
        assert "--port" in result.output
        assert "--db" in result.output

    def test_serve_in_group(self, runner):
        result = runner.invoke(main, ["--help"])
        assert "serve" in result.output
