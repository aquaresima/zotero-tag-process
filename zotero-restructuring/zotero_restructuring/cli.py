"""
cli.py — Click-based CLI entry point (Phase 3 skeleton).

Subcommands ingest, simulate, and validate are stubbed.
Full implementation deferred to Phase 3.
"""

from __future__ import annotations

from pathlib import Path

import click

from .config import (
    get_zotero_sqlite_path,
    SIMULATION_DB_PATH,
)


@click.group()
@click.version_option(package_name="zotero-restructuring")
def main() -> None:
    """ZoteroRestructuring: safe sandbox reorganization of a Zotero library."""


@main.command()
@click.option(
    "--sqlite",
    "sqlite_path",
    default=None,
    type=click.Path(exists=False, dir_okay=False),
    help="Path to zotero.sqlite (overrides ZOTERO_SQLITE_PATH env var).",
)
def ingest(sqlite_path: str | None) -> None:
    """Phase 1: Read zotero.sqlite and report library statistics."""
    from .reader import read_library
    from .library import Library

    path = Path(sqlite_path) if sqlite_path else get_zotero_sqlite_path()
    click.echo(f"Reading: {path}")
    raw = read_library(path)
    lib = Library.from_raw(raw)
    stats = lib.stats()
    click.echo(
        f"Ingested: {stats['items']} items, "
        f"{stats['collections']} collections, "
        f"{stats['tags']} tags "
        f"({stats['orphaned_items']} orphaned items)"
    )


@main.command()
@click.option(
    "--sqlite",
    "sqlite_path",
    default=None,
    type=click.Path(exists=False, dir_okay=False),
    help="Path to zotero.sqlite (overrides ZOTERO_SQLITE_PATH env var).",
)
@click.option(
    "--output",
    "output_path",
    default=None,
    type=click.Path(dir_okay=False),
    help=f"Path for simulation database (default: {SIMULATION_DB_PATH}).",
)
def simulate(sqlite_path: str | None, output_path: str | None) -> None:
    """Phase 2: Clone library into a simulation database (interactive mode deferred)."""
    from .reader import read_library
    from .library import Library
    from .simulation import clone

    path = Path(sqlite_path) if sqlite_path else get_zotero_sqlite_path()
    db_path = Path(output_path) if output_path else SIMULATION_DB_PATH

    click.echo(f"Reading: {path}")
    raw = read_library(path)
    lib = Library.from_raw(raw)
    click.echo(f"Cloning into: {db_path}")
    sim = clone(lib, db_path=db_path)
    click.echo("Simulation database ready.")
    click.echo(
        "Interactive reorganization (Phase 3) is not yet implemented. "
        "Use the Python API directly for now."
    )


@main.command()
@click.option(
    "--sqlite",
    "sqlite_path",
    default=None,
    type=click.Path(exists=False, dir_okay=False),
    help="Path to zotero.sqlite (overrides ZOTERO_SQLITE_PATH env var).",
)
def validate(sqlite_path: str | None) -> None:
    """Run consistency checks on the ingested library."""
    from .reader import read_library
    from .library import Library
    from .validation import validate as run_validate, ValidationError

    path = Path(sqlite_path) if sqlite_path else get_zotero_sqlite_path()
    raw = read_library(path)
    lib = Library.from_raw(raw)
    try:
        report = run_validate(lib)
        click.echo(report.summary())
    except ValidationError as exc:
        click.echo(f"FATAL: {exc}", err=True)
        raise SystemExit(1)


@main.command(name="import-tags")
@click.option(
    "--generated-db",
    "generated_db",
    required=True,
    type=click.Path(exists=True, dir_okay=False),
    help="Path to zotero_generatedtags_*.sqlite (from the tag-generation tool).",
)
@click.option(
    "--sqlite",
    "sqlite_path",
    default=None,
    type=click.Path(exists=False, dir_okay=False),
    help="Path to zotero.sqlite (overrides ZOTERO_SQLITE_PATH env var).",
)
@click.option(
    "--db",
    "db_path",
    default=None,
    type=click.Path(dir_okay=False),
    help=f"Path to the simulation database (default: {SIMULATION_DB_PATH}).",
)
def import_tags(generated_db: str, sqlite_path: str | None, db_path: str | None) -> None:
    """Ingest zotero.sqlite and import approved tags from a generatedtags DB."""
    from .import_tags import import_tags as run_import

    sim_path = Path(db_path) if db_path else SIMULATION_DB_PATH
    zp = Path(sqlite_path) if sqlite_path else None
    click.echo(f"Importing approved tags from: {generated_db}")
    result = run_import(Path(generated_db), sim_db_path=sim_path, zotero_sqlite_path=zp)
    click.echo(
        f"Matched {result.items_matched} item(s), "
        f"imported {result.proposals_imported} proposal(s), "
        f"{result.skipped_existing} skipped (already tagged), "
        f"{result.skipped_unmatched} unmatched."
    )
    for err in result.errors:
        click.echo(f"  ! {err}", err=True)


@main.command()
@click.option("--port", default=8000, type=int, show_default=True, help="HTTP port.")
@click.option(
    "--db",
    "db_path",
    default=None,
    type=click.Path(dir_okay=False),
    help=f"Path to the simulation database (default: {SIMULATION_DB_PATH}).",
)
def serve(port: int, db_path: str | None) -> None:
    """Phase 3b: launch the FastAPI web interface and open a browser."""
    import subprocess
    import urllib.request
    import uvicorn

    from .web.app import create_app

    path = Path(db_path) if db_path else SIMULATION_DB_PATH
    app = create_app(path)
    url = f"http://localhost:{port}"

    # Start Ollama if not already running — track as child so we can stop it on exit
    _ollama_proc = None
    try:
        urllib.request.urlopen("http://localhost:11434/api/tags", timeout=2)
    except Exception:
        try:
            _ollama_proc = subprocess.Popen(
                ["ollama", "serve"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            click.echo("  → started ollama serve (will stop on exit)")
        except FileNotFoundError:
            pass  # ollama not installed — user must start it manually

    click.echo("")
    click.echo("  Zotero Restructuring — web UI")
    click.echo(f"  → {url}")
    click.echo(f"  → db: {path}")
    click.echo("")
    click.echo("  Press Ctrl+C to stop.")
    click.echo("")

    try:
        import webbrowser
        webbrowser.open(url)
    except Exception:  # noqa: BLE001
        pass

    try:
        uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")
    finally:
        if _ollama_proc is not None:
            _ollama_proc.terminate()
            click.echo("  → stopped ollama serve")

if __name__ == '__main__':
    main()
