"""Smoke tests for the combined zotero-tag-process web app and CLI."""

from __future__ import annotations

from click.testing import CliRunner
from starlette.testclient import TestClient

from zotero_tag_process.cli import main
from zotero_tag_process.web.app import create_app


def _client() -> TestClient:
    return TestClient(create_app())


def test_landing_page_has_two_workflow_cards():
    r = _client().get("/")
    assert r.status_code == 200
    assert 'href="/generate/"' in r.text
    assert 'href="/update/"' in r.text
    assert "Tag Generation" in r.text
    assert "Zotero Update" in r.text


def test_generate_mounted_and_urls_prefixed():
    c = _client()
    assert c.get("/generate/").status_code == 200
    assert c.get("/generate/api/health").status_code == 200
    html = c.get("/generate/").text
    # root-relative URLs are rewritten to carry the mount prefix
    assert "/generate/api/status" in html
    assert "/generate/review" in html


def test_update_mounted_and_urls_prefixed():
    c = _client()
    assert c.get("/update/").status_code == 200
    assert c.get("/update/api/health").status_code == 200
    assert "/update/stats" in c.get("/update/").text


def test_external_cdn_url_not_rewritten():
    html = _client().get("/generate/").text
    assert "https://cdn.tailwindcss.com" in html
    assert "https://cdn.tailwindcss.com".replace("//", "//generate/") not in html


def test_cli_help_lists_all_commands():
    res = CliRunner().invoke(main, ["--help"])
    assert res.exit_code == 0
    for cmd in ("serve", "ingest", "generate", "import-tags", "help"):
        assert cmd in res.output


def test_cli_help_subcommand_prints_narrative():
    res = CliRunner().invoke(main, ["help"])
    assert res.exit_code == 0
    assert "WORKFLOW A" in res.output
    assert "WORKFLOW B" in res.output
