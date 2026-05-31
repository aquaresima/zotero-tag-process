"""
export.py — Export simulation state to SQLite or Zotero API (Phase 4 skeleton).

Interface is defined here; implementation is deferred to Phase 4.
All methods raise NotImplementedError.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .simulation import SimulationDB
    from .validation import ValidationReport


def export_to_sqlite(
    sim: "SimulationDB",
    output_path: Path,
    *,
    backup_original: bool = True,
) -> Path:
    """
    Export simulation state to a new SQLite file compatible with Zotero.

    Parameters
    ----------
    sim:
        The SimulationDB containing the modified library state.
    output_path:
        Where to write the resulting Zotero-compatible SQLite file.
    backup_original:
        If True, back up the original database before writing.

    Returns
    -------
    Path
        Path to the written output file.

    Raises
    ------
    NotImplementedError
        Phase 4 is not yet implemented.
    """
    raise NotImplementedError(
        "export_to_sqlite is deferred to Phase 4. "
        "Use the simulation database directly for Phase 2 operations."
    )


def upload_to_zotero_api(
    sim: "SimulationDB",
    validation_report: "ValidationReport",
    *,
    dry_run: bool = True,
) -> dict:
    """
    Push simulation changes to the live Zotero library via the API.

    Parameters
    ----------
    sim:
        The SimulationDB with pending changes.
    validation_report:
        Must be valid (no fatal errors) before upload is permitted.
    dry_run:
        If True, log the would-be API calls without executing them.

    Returns
    -------
    dict
        Summary of upload results (items updated, errors, etc.).

    Raises
    ------
    NotImplementedError
        Phase 4 is not yet implemented.
    """
    raise NotImplementedError(
        "upload_to_zotero_api is deferred to Phase 4 and requires "
        "ZOTERO_API_KEY to be set."
    )
