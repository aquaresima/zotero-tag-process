"""
test_cli.py -- Tests for cli.py (Click CLI entry point).

Covers:
- ingest subcommand with test fixture
- simulate subcommand with test fixture
- validate subcommand with test fixture
- version option
"""

from __future__ import annotations

import pytest
from click.testing import CliRunner

from zotero_restructuring.cli import main


@pytest.fixture
def runner():
    return CliRunner()


class TestIngest:
    def test_ingest_with_valid_sqlite(self, runner, small_zotero_sqlite):
        result = runner.invoke(main, ["ingest", "--sqlite", str(small_zotero_sqlite)])
        assert result.exit_code == 0
        assert "10 items" in result.output
        assert "3 collections" in result.output
        assert "15 tags" in result.output

    def test_ingest_missing_file(self, runner, tmp_path):
        missing = tmp_path / "nonexistent.sqlite"
        result = runner.invoke(main, ["ingest", "--sqlite", str(missing)])
        assert result.exit_code != 0


class TestSimulate:
    def test_simulate_with_valid_sqlite(self, runner, small_zotero_sqlite):
        from zotero_restructuring.config import DATA_DIR
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        output = DATA_DIR / "test_cli_sim.sqlite"
        result = runner.invoke(main, [
            "simulate",
            "--sqlite", str(small_zotero_sqlite),
            "--output", str(output),
        ])
        assert result.exit_code == 0
        assert "Simulation database ready" in result.output
        # Clean up
        if output.exists():
            output.unlink()


class TestValidate:
    def test_validate_with_valid_sqlite(self, runner, small_zotero_sqlite):
        result = runner.invoke(main, ["validate", "--sqlite", str(small_zotero_sqlite)])
        assert result.exit_code == 0
        assert "ValidationReport" in result.output


class TestVersion:
    def test_version_option(self, runner):
        result = runner.invoke(main, ["--version"])
        assert result.exit_code == 0
        assert "version" in result.output.lower()
