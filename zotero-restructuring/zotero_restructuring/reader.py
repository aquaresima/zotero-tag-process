"""
reader.py — Read-only access to the source zotero.sqlite.

Extracts collections, items (non-attachment), tags, and their relationships
into raw Python dicts.  Never opens the source database in write mode.

Failure modes handled:
- File not found: FileNotFoundError with actionable message
- Locked database: retries with ?mode=ro URI, then falls back to temp copy
- Schema mismatch: emits SchemaWarning, continues best-effort
- Empty database: returns empty dicts
"""

from __future__ import annotations

import shutil
import sqlite3
import tempfile
import warnings
from pathlib import Path
from typing import Any

from . import schema as S
from .config import get_zotero_sqlite_path


class SchemaWarning(UserWarning):
    """Emitted when the source database is missing expected tables."""


# ── Public helpers ────────────────────────────────────────────────────────────

def _open_connection(path: Path) -> sqlite3.Connection:
    """Open a read-only SQLite connection, falling back to a temp copy."""
    uri = path.as_uri() + "?mode=ro"
    try:
        conn = sqlite3.connect(uri, uri=True)
        conn.row_factory = sqlite3.Row
        # probe — will raise if locked and we can't read
        conn.execute("SELECT 1")
        return conn
    except sqlite3.OperationalError:
        # Close the failed connection if it was created
        try:
            conn.close()
        except (sqlite3.OperationalError, UnboundLocalError):
            pass

    # Fallback: copy to a temp file and open normally
    tmp = tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False)
    tmp.close()
    shutil.copy2(path, tmp.name)
    conn = sqlite3.connect(tmp.name)
    conn.row_factory = sqlite3.Row
    return conn


def _validate_path(path: Path) -> None:
    """Raise FileNotFoundError if the SQLite file is absent or unreadable."""
    if not path.exists():
        raise FileNotFoundError(
            f"Zotero database not found at '{path}'. "
            "Set the correct path via the ZOTERO_SQLITE_PATH environment variable "
            "or the --sqlite CLI argument."
        )
    if not path.is_file():
        raise FileNotFoundError(f"'{path}' is not a regular file.")
    if path.suffix.lower() != ".sqlite":
        raise ValueError(
            f"'{path}' does not have a .sqlite extension. "
            "Verify the path points to a Zotero database."
        )


def _check_schema(conn: sqlite3.Connection) -> None:
    """Warn if expected tables are absent in the connected database."""
    cursor = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    )
    present = {row["name"] for row in cursor.fetchall()}
    missing = S.EXPECTED_TABLES - present
    if missing:
        warnings.warn(
            f"The following expected tables are missing from the database: "
            f"{sorted(missing)}. Ingestion will be best-effort.",
            SchemaWarning,
            stacklevel=3,
        )


# ── Raw extraction functions ──────────────────────────────────────────────────

def _read_item_types(conn: sqlite3.Connection) -> dict[int, str]:
    """Return {itemTypeID -> typeName}."""
    try:
        rows = conn.execute(
            f"SELECT itemTypeID, typeName FROM {S.ITEM_TYPES_TABLE}"
        ).fetchall()
        return {row["itemTypeID"]: row["typeName"] for row in rows}
    except sqlite3.OperationalError:
        return {}


def _read_fields(conn: sqlite3.Connection) -> dict[int, str]:
    """Return {fieldID -> fieldName} from fieldsCombined view (falls back to fields)."""
    for table in (S.FIELDS_COMBINED_VIEW, S.FIELDS_TABLE):
        try:
            rows = conn.execute(
                f"SELECT fieldID, fieldName FROM {table}"
            ).fetchall()
            return {row["fieldID"]: row["fieldName"] for row in rows}
        except sqlite3.OperationalError:
            continue
    return {}


def _read_deleted_item_ids(conn: sqlite3.Connection) -> set[int]:
    """Return set of itemIDs that are in the trash."""
    try:
        rows = conn.execute(
            f"SELECT itemID FROM {S.DELETED_ITEMS_TABLE}"
        ).fetchall()
        return {row["itemID"] for row in rows}
    except sqlite3.OperationalError:
        return set()


def _read_attachment_item_ids(conn: sqlite3.Connection) -> set[int]:
    """Return set of itemIDs that are attachments (not standalone references)."""
    try:
        rows = conn.execute(
            f"SELECT itemID FROM {S.ITEM_ATTACHMENTS_TABLE}"
        ).fetchall()
        return {row["itemID"] for row in rows}
    except sqlite3.OperationalError:
        return set()


def _read_raw_items(
    conn: sqlite3.Connection,
    item_types: dict[int, str],
    fields: dict[int, str],
    deleted_ids: set[int],
    attachment_ids: set[int],
) -> dict[int, dict[str, Any]]:
    """
    Return raw item dicts keyed by itemID.

    Filters applied:
    - Only items from the primary library (libraryID = PRIMARY_LIBRARY_ID)
    - Excludes deleted items
    - Excludes pure attachments (items in itemAttachments table)
    - Excludes non-bibliographic item types (annotation, note, attachment)
    """
    # Build set of excluded itemTypeIDs from the type name exclusion list
    excluded_type_ids: set[int] = {
        tid for tid, tname in item_types.items()
        if tname in S.EXCLUDED_ITEM_TYPE_NAMES
    }

    try:
        rows = conn.execute(
            f"SELECT itemID, itemTypeID, dateAdded, dateModified, libraryID, key "
            f"FROM {S.ITEMS_TABLE} "
            f"WHERE libraryID = ?",
            (S.PRIMARY_LIBRARY_ID,),
        ).fetchall()
    except sqlite3.OperationalError:
        return {}

    # Build itemID -> {fieldName -> value} metadata map
    metadata: dict[int, dict[str, str]] = {}
    try:
        meta_rows = conn.execute(
            f"SELECT d.itemID, d.fieldID, v.value "
            f"FROM {S.ITEM_DATA_TABLE} d "
            f"JOIN {S.ITEM_DATA_VALUES_TABLE} v ON d.valueID = v.valueID"
        ).fetchall()
        for mr in meta_rows:
            iid = mr["itemID"]
            fname = fields.get(mr["fieldID"], f"field_{mr['fieldID']}")
            metadata.setdefault(iid, {})[fname] = mr["value"]
    except sqlite3.OperationalError:
        pass

    # Build itemID -> [creator_string] map
    creators_map: dict[int, list[str]] = {}
    try:
        creator_rows = conn.execute(
            f"SELECT ic.itemID, c.firstName, c.lastName, c.fieldMode "
            f"FROM {S.ITEM_CREATORS_TABLE} ic "
            f"JOIN {S.CREATORS_TABLE} c ON ic.creatorID = c.creatorID "
            f"ORDER BY ic.itemID, ic.orderIndex"
        ).fetchall()
        for cr in creator_rows:
            iid = cr["itemID"]
            if cr["fieldMode"] == 1:  # institution / single-field name
                name = cr["lastName"] or ""
            else:
                parts = [cr["firstName"] or "", cr["lastName"] or ""]
                name = " ".join(p for p in parts if p).strip()
            creators_map.setdefault(iid, []).append(name)
    except sqlite3.OperationalError:
        pass

    items: dict[int, dict[str, Any]] = {}
    for row in rows:
        iid = row["itemID"]
        if iid in deleted_ids:
            continue
        if iid in attachment_ids:
            continue
        if row["itemTypeID"] in excluded_type_ids:
            continue

        m = metadata.get(iid, {})
        items[iid] = {
            "item_id": iid,
            "key": row["key"],
            "item_type": item_types.get(row["itemTypeID"], "unknown"),
            "title": m.get(S.FIELD_TITLE),
            "date": m.get(S.FIELD_DATE),
            "doi": m.get(S.FIELD_DOI),
            "url": m.get(S.FIELD_URL),
            "creators": creators_map.get(iid, []),
            "date_added": row["dateAdded"],
            "metadata": m,
        }
    return items


def _read_raw_collections(conn: sqlite3.Connection) -> dict[int, dict[str, Any]]:
    """Return raw collection dicts keyed by collectionID.

    Only returns collections from the primary library (libraryID = PRIMARY_LIBRARY_ID).
    """
    try:
        rows = conn.execute(
            f"SELECT collectionID, collectionName, parentCollectionID, key "
            f"FROM {S.COLLECTIONS_TABLE} "
            f"WHERE libraryID = ?",
            (S.PRIMARY_LIBRARY_ID,),
        ).fetchall()
    except sqlite3.OperationalError:
        return {}
    return {
        row["collectionID"]: {
            "collection_id": row["collectionID"],
            "name": row["collectionName"],
            "parent_id": row["parentCollectionID"],
            "key": row["key"],
        }
        for row in rows
    }


def _read_raw_tags(conn: sqlite3.Connection) -> dict[int, dict[str, Any]]:
    """Return raw tag dicts keyed by tagID."""
    try:
        rows = conn.execute(
            f"SELECT tagID, name FROM {S.TAGS_TABLE}"
        ).fetchall()
    except sqlite3.OperationalError:
        return {}
    return {
        row["tagID"]: {"tag_id": row["tagID"], "name": row["name"]}
        for row in rows
    }


def _read_raw_item_tags(conn: sqlite3.Connection) -> list[dict[str, int]]:
    """Return list of {item_id, tag_id} dicts."""
    try:
        rows = conn.execute(
            f"SELECT itemID, tagID FROM {S.ITEM_TAGS_TABLE}"
        ).fetchall()
        return [{"item_id": row["itemID"], "tag_id": row["tagID"]} for row in rows]
    except sqlite3.OperationalError:
        return []


def _read_raw_collection_items(conn: sqlite3.Connection) -> list[dict[str, int]]:
    """Return list of {collection_id, item_id} dicts."""
    try:
        rows = conn.execute(
            f"SELECT collectionID, itemID FROM {S.COLLECTION_ITEMS_TABLE}"
        ).fetchall()
        return [
            {"collection_id": row["collectionID"], "item_id": row["itemID"]}
            for row in rows
        ]
    except sqlite3.OperationalError:
        return []


# ── Public API ────────────────────────────────────────────────────────────────

class RawLibraryData:
    """Container for raw extraction results before Library construction."""

    __slots__ = ("items", "collections", "tags", "item_tags", "collection_items")

    def __init__(
        self,
        items: dict[int, dict[str, Any]],
        collections: dict[int, dict[str, Any]],
        tags: dict[int, dict[str, Any]],
        item_tags: list[dict[str, int]],
        collection_items: list[dict[str, int]],
    ) -> None:
        self.items = items
        self.collections = collections
        self.tags = tags
        self.item_tags = item_tags
        self.collection_items = collection_items


def read_library(path: Path | None = None) -> "RawLibraryData":
    """
    Read the Zotero source database and return raw extraction data.

    Parameters
    ----------
    path:
        Path to zotero.sqlite.  If None, uses config.get_zotero_sqlite_path().

    Raises
    ------
    FileNotFoundError
        If the database file does not exist or is not a regular file.
    ValueError
        If the path does not have a .sqlite extension.
    """
    if path is None:
        path = get_zotero_sqlite_path()

    _validate_path(path)
    conn = _open_connection(path)

    try:
        _check_schema(conn)
        item_types = _read_item_types(conn)
        fields = _read_fields(conn)
        deleted_ids = _read_deleted_item_ids(conn)
        attachment_ids = _read_attachment_item_ids(conn)
        items = _read_raw_items(conn, item_types, fields, deleted_ids, attachment_ids)
        collections = _read_raw_collections(conn)
        tags = _read_raw_tags(conn)
        item_tags = _read_raw_item_tags(conn)
        collection_items = _read_raw_collection_items(conn)
    finally:
        conn.close()

    return RawLibraryData(
        items=items,
        collections=collections,
        tags=tags,
        item_tags=item_tags,
        collection_items=collection_items,
    )
