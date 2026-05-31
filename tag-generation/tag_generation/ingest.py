"""
ingest.py — Build the generatedtags DB from a Zotero source database.

Clones ``zotero.sqlite`` into the output DB using the shared
``zotero_restructuring.simulation.clone`` logic (full normalization, junk/MeSH
seeding, ``/unread`` stripping) and enables SQLite WAL mode so the Ollama
writer and the review-page reader can operate concurrently.
"""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import create_engine, text

from .config import ensure_shared_on_path

ensure_shared_on_path()

from zotero_restructuring.library import Library  # noqa: E402
from zotero_restructuring.reader import read_library  # noqa: E402
from zotero_restructuring.simulation import clone  # noqa: E402


def allow_output_path(db_path: Path) -> None:
    """Whitelist ``db_path``'s parent dir in the shared config write-guard.

    The shared ``SimulationDB`` validates every write against
    ``zotero_restructuring.config.ALLOWED_WRITE_PATHS``; the generatedtags DB
    lives outside that tree, so its directory is appended here at runtime.
    """
    from zotero_restructuring import config as zr_config

    parent = db_path.expanduser().resolve().parent
    if parent not in zr_config.ALLOWED_WRITE_PATHS:
        zr_config.ALLOWED_WRITE_PATHS = zr_config.ALLOWED_WRITE_PATHS + (parent,)


def enable_wal(db_path: Path) -> None:
    """Switch the SQLite database at ``db_path`` into WAL journal mode."""
    engine = create_engine(f"sqlite:///{db_path}")
    try:
        with engine.connect() as conn:
            conn.execute(text("PRAGMA journal_mode=WAL"))
            conn.commit()
    finally:
        engine.dispose()


def ingest(
    zotero_sqlite_path: Path,
    output_db_path: Path,
) -> dict:
    """Ingest + normalize ``zotero_sqlite_path`` into ``output_db_path``.

    Processes ALL papers (no tag-count filter).  Returns a small stats dict.
    """
    output_db_path.parent.mkdir(parents=True, exist_ok=True)
    allow_output_path(output_db_path)
    raw = read_library(zotero_sqlite_path)
    lib = Library.from_raw(raw)
    sim = clone(
        lib,
        db_path=output_db_path,
        zotero_sqlite_path=zotero_sqlite_path,
    )
    sim.close()
    enable_wal(output_db_path)
    return {
        "items": len(lib.items),
        "tags": len(lib.tags),
        "collections": len(lib.collections),
        "output": str(output_db_path),
    }
