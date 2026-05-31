"""
cli.py — unified Click entry point for the Zotero tag enrichment pipeline.

Commands:
    serve        Launch the combined FastAPI web UI (both workflows).
    ingest       Delegate to tag_generation.ingest (zotero.sqlite -> generatedtags DB).
    generate     Delegate to tag_generation.worker.run_worker (Ollama batch tagger).
    import-tags  Delegate to zotero_restructuring.import_tags (import approved tags).
    help         Print the narrative help guide to stdout.
"""

from __future__ import annotations

from pathlib import Path

import click

from tag_generation.config import (
    OLLAMA_MODEL,
    WORKER_BATCH_SIZE,
    default_generatedtags_path,
    get_zotero_sqlite_path,
)
from zotero_restructuring.config import SIMULATION_DB_PATH

from .help_text import HELP_TEXT


@click.group()
@click.version_option(package_name="zotero-tag-process")
def main() -> None:
    """zotero-tag-process: unified Zotero tag generation + application pipeline."""


# ── serve ──────────────────────────────────────────────────────────────────


@main.command()
@click.option("--port", default=8000, show_default=True, type=int, help="HTTP port.")
def serve(port: int) -> None:
    """Launch the combined FastAPI web UI (Tag Generation + Zotero Update)."""
    import subprocess
    import urllib.request
    import webbrowser

    import uvicorn

    from .web.app import create_app

    app = create_app()
    url = f"http://localhost:{port}"

    _ollama_proc = None
    try:
        urllib.request.urlopen("http://localhost:11434/api/tags", timeout=2)
    except Exception:
        try:
            _ollama_proc = subprocess.Popen(
                ["ollama", "serve"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            click.echo("  -> started ollama serve (will stop on exit)")
        except FileNotFoundError:
            pass

    click.echo("")
    click.echo("  zotero-tag-process - unified web UI")
    click.echo(f"  -> {url}")
    click.echo(f"  -> generate:  {url}/generate/")
    click.echo(f"  -> update:    {url}/update/")
    click.echo("")
    click.echo("  Press Ctrl+C to stop.")
    click.echo("")

    try:
        webbrowser.open(url)
    except Exception:  # noqa: BLE001
        pass

    try:
        uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")
    finally:
        if _ollama_proc is not None:
            _ollama_proc.terminate()
            click.echo("  -> stopped ollama serve")


# ── ingest ─────────────────────────────────────────────────────────────────


@main.command()
@click.option("--sqlite", "sqlite_path", default=None,
              type=click.Path(exists=False, dir_okay=False),
              help="Path to zotero.sqlite (overrides ZOTERO_SQLITE_PATH).")
@click.option("--output", "output_path", default=None,
              type=click.Path(dir_okay=False),
              help="Output generatedtags DB path.")
def ingest(sqlite_path: str | None, output_path: str | None) -> None:
    """Ingest + normalize zotero.sqlite into a generatedtags DB (ALL papers)."""
    from tag_generation.ingest import ingest as run_ingest

    src = Path(sqlite_path) if sqlite_path else get_zotero_sqlite_path()
    out = Path(output_path) if output_path else default_generatedtags_path()
    click.echo(f"Ingesting: {src}")
    click.echo(f"Output:    {out}")
    stats = run_ingest(src, out)
    click.echo(
        f"Done: {stats['items']} items, {stats['tags']} tags, "
        f"{stats['collections']} collections."
    )


# ── generate ───────────────────────────────────────────────────────────────


@main.command()
@click.option("--db", "db_path", default=None,
              type=click.Path(dir_okay=False),
              help="Path to the generatedtags DB.")
@click.option("--model", default=OLLAMA_MODEL, show_default=True,
              help="Ollama model name.")
@click.option("--batch-size", default=WORKER_BATCH_SIZE, show_default=True, type=int)
def generate(db_path: str | None, model: str, batch_size: int) -> None:
    """Run the Ollama batch tagger; writes proposals in real time."""
    from tag_generation.worker import run_worker

    path = Path(db_path) if db_path else default_generatedtags_path()
    click.echo(f"Generating tags on: {path}")
    result = run_worker(path, ollama_model=model, batch_size=batch_size)
    click.echo(
        f"Processed {result.items_processed} item(s), "
        f"wrote {result.proposals_written} proposal(s), "
        f"{result.low_confidence_items} low-confidence, "
        f"{result.skipped_decided} skipped (already decided)."
    )
    for err in result.errors:
        click.echo(f"  ! {err}", err=True)


# ── import-tags ──────────────────────────────────────────────────────────────


@main.command(name="import-tags")
@click.option("--generated-db", "generated_db", required=True,
              type=click.Path(exists=True, dir_okay=False),
              help="Path to zotero_generatedtags_*.sqlite (from tag generation).")
@click.option("--sqlite", "sqlite_path", default=None,
              type=click.Path(exists=False, dir_okay=False),
              help="Path to zotero.sqlite (overrides ZOTERO_SQLITE_PATH).")
def import_tags(generated_db: str, sqlite_path: str | None) -> None:
    """Ingest zotero.sqlite and import approved tags from a generatedtags DB."""
    from zotero_restructuring.import_tags import import_tags as run_import

    zp = Path(sqlite_path) if sqlite_path else None
    click.echo(f"Importing approved tags from: {generated_db}")
    result = run_import(
        Path(generated_db),
        sim_db_path=SIMULATION_DB_PATH,
        zotero_sqlite_path=zp,
    )
    click.echo(
        f"Matched {result.items_matched} item(s), "
        f"imported {result.proposals_imported} proposal(s), "
        f"{result.skipped_existing} skipped (already tagged), "
        f"{result.skipped_unmatched} unmatched."
    )
    for err in result.errors:
        click.echo(f"  ! {err}", err=True)


# ── help ──────────────────────────────────────────────────────────────────────


@main.command()
def help() -> None:
    """Print the narrative pipeline guide."""
    click.echo(HELP_TEXT)


if __name__ == "__main__":
    main()
