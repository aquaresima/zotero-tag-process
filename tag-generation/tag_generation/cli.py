"""
cli.py — Click entry point for the tag-generation tool.

Commands:
    ingest    Clone + normalize a zotero.sqlite into a generatedtags DB.
    generate  Run the Ollama batch tagger against a generatedtags DB.
    serve     Launch the FastAPI web UI (setup + real-time review).
"""

from __future__ import annotations

from pathlib import Path

import click

from .config import (
    OLLAMA_MODEL,
    WORKER_BATCH_SIZE,
    WORKER_MIN_TAGS,
    default_generatedtags_path,
    get_zotero_sqlite_path,
)


@click.group()
@click.version_option(package_name="tag-generation")
def main() -> None:
    """tag-generation: generate, review, and export LLM tags for a Zotero library."""


@main.command()
@click.option("--sqlite", "sqlite_path", default=None,
              type=click.Path(exists=False, dir_okay=False),
              help="Path to zotero.sqlite (overrides ZOTERO_SQLITE_PATH).")
@click.option("--output", "output_path", default=None,
              type=click.Path(dir_okay=False),
              help="Output generatedtags DB path.")
def ingest(sqlite_path: str | None, output_path: str | None) -> None:
    """Ingest + normalize zotero.sqlite into a generatedtags DB (ALL papers)."""
    from .ingest import ingest as run_ingest

    src = Path(sqlite_path) if sqlite_path else get_zotero_sqlite_path()
    out = Path(output_path) if output_path else default_generatedtags_path()
    click.echo(f"Ingesting: {src}")
    click.echo(f"Output:    {out}")
    stats = run_ingest(src, out)
    click.echo(
        f"Done: {stats['items']} items, {stats['tags']} tags, "
        f"{stats['collections']} collections."
    )


@main.command()
@click.option("--db", "db_path", default=None,
              type=click.Path(dir_okay=False),
              help="Path to the generatedtags DB.")
@click.option("--model", default=OLLAMA_MODEL, show_default=True,
              help="Ollama model name.")
@click.option("--batch-size", default=WORKER_BATCH_SIZE, show_default=True, type=int)
@click.option("--min-tags", default=WORKER_MIN_TAGS, show_default=True, type=int)
def generate(db_path: str | None, model: str, batch_size: int, min_tags: int) -> None:
    """Run the Ollama batch tagger; writes proposals in real time."""
    from .worker import run_worker

    path = Path(db_path) if db_path else default_generatedtags_path()
    click.echo(f"Generating tags on: {path}")
    result = run_worker(
        path, ollama_model=model, batch_size=batch_size, min_tags=min_tags,
    )
    click.echo(
        f"Processed {result.items_processed} item(s), "
        f"wrote {result.proposals_written} proposal(s), "
        f"{result.low_confidence_items} low-confidence, "
        f"{result.skipped_decided} skipped (already decided)."
    )
    for err in result.errors:
        click.echo(f"  ! {err}", err=True)


@main.command()
@click.option("--port", default=8001, show_default=True, type=int)
@click.option("--db", "db_path", default=None,
              type=click.Path(dir_okay=False),
              help="Path to the generatedtags DB.")
def serve(port: int, db_path: str | None) -> None:
    """Launch the FastAPI web UI (setup + real-time review)."""
    import subprocess
    import urllib.request
    import webbrowser

    import uvicorn

    from .web.app import create_app

    path = Path(db_path) if db_path else default_generatedtags_path()
    app = create_app(path)
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
    click.echo("  tag-generation - web UI")
    click.echo(f"  -> {url}")
    click.echo(f"  -> db: {path}")
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


if __name__ == "__main__":
    main()
