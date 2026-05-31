"""
conftest.py — Shared pytest fixtures.

Provides:
- small_zotero_sqlite: a temporary SQLite file with Zotero-compatible schema,
  10 items, 3 collections, 15 tags.
- sample_library: a Library constructed from small_zotero_sqlite.
- sim_db_path: a temporary path for a simulation database.
"""

from __future__ import annotations

import sqlite3
import tempfile
from pathlib import Path

import pytest

from zotero_restructuring.library import Library
from zotero_restructuring.reader import read_library


# ── Schema DDL matching real Zotero 7 structure ───────────────────────────────

_DDL = """
CREATE TABLE IF NOT EXISTS libraries (
    libraryID INTEGER PRIMARY KEY,
    type TEXT NOT NULL,
    editable INT NOT NULL DEFAULT 1,
    filesEditable INT NOT NULL DEFAULT 1,
    version INT NOT NULL DEFAULT 0,
    storageVersion INT NOT NULL DEFAULT 0,
    lastSync INT NOT NULL DEFAULT 0,
    archived INT NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS itemTypes (
    itemTypeID INTEGER PRIMARY KEY,
    typeName TEXT,
    templateItemTypeID INT,
    display INT DEFAULT 1
);

CREATE TABLE IF NOT EXISTS fields (
    fieldID INTEGER PRIMARY KEY,
    fieldName TEXT,
    fieldFormatID INT
);

CREATE TABLE IF NOT EXISTS fieldsCombined (
    fieldID INTEGER PRIMARY KEY,
    fieldName TEXT
);

CREATE TABLE IF NOT EXISTS items (
    itemID INTEGER PRIMARY KEY,
    itemTypeID INT NOT NULL,
    dateAdded TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    dateModified TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    clientDateModified TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    libraryID INT NOT NULL,
    key TEXT NOT NULL,
    version INT NOT NULL DEFAULT 0,
    synced INT NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS itemDataValues (
    valueID INTEGER PRIMARY KEY,
    value UNIQUE
);

CREATE TABLE IF NOT EXISTS itemData (
    itemID INT,
    fieldID INT,
    valueID INT,
    PRIMARY KEY (itemID, fieldID)
);

CREATE TABLE IF NOT EXISTS creators (
    creatorID INTEGER PRIMARY KEY,
    firstName TEXT,
    lastName TEXT,
    fieldMode INT DEFAULT 0
);

CREATE TABLE IF NOT EXISTS itemCreators (
    itemID INT NOT NULL,
    creatorID INT NOT NULL,
    creatorTypeID INT NOT NULL DEFAULT 1,
    orderIndex INT NOT NULL DEFAULT 0,
    PRIMARY KEY (itemID, creatorID, creatorTypeID, orderIndex),
    UNIQUE (itemID, orderIndex)
);

CREATE TABLE IF NOT EXISTS collections (
    collectionID INTEGER PRIMARY KEY,
    collectionName TEXT NOT NULL,
    parentCollectionID INT DEFAULT NULL,
    clientDateModified TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    libraryID INT NOT NULL DEFAULT 1,
    key TEXT NOT NULL,
    version INT NOT NULL DEFAULT 0,
    synced INT NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS collectionItems (
    collectionID INT NOT NULL,
    itemID INT NOT NULL,
    orderIndex INT NOT NULL DEFAULT 0,
    PRIMARY KEY (collectionID, itemID)
);

CREATE TABLE IF NOT EXISTS tags (
    tagID INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS itemTags (
    itemID INT NOT NULL,
    tagID INT NOT NULL,
    type INT NOT NULL DEFAULT 0,
    PRIMARY KEY (itemID, tagID)
);

CREATE TABLE IF NOT EXISTS itemAttachments (
    itemID INTEGER PRIMARY KEY,
    parentItemID INT,
    linkMode INT,
    contentType TEXT,
    charsetID INT,
    path TEXT
);

CREATE TABLE IF NOT EXISTS deletedItems (
    itemID INTEGER PRIMARY KEY,
    dateDeleted DEFAULT CURRENT_TIMESTAMP NOT NULL
);
"""


def _build_test_db(path: Path) -> None:
    """Populate a fresh SQLite file with test data: 10 items, 3 collections, 15 tags."""
    conn = sqlite3.connect(str(path))
    conn.executescript(_DDL)

    # Libraries: personal (1), group (2), feed (3)
    conn.execute("INSERT INTO libraries VALUES (1,'user',1,1,0,0,0,0)")
    conn.execute("INSERT INTO libraries VALUES (2,'group',1,1,0,0,0,0)")
    conn.execute("INSERT INTO libraries VALUES (3,'feed',0,0,0,0,0,0)")

    # Item types (including excluded types: annotation, note, attachment)
    for tid, name in [
        (2, "journalArticle"), (3, "book"), (4, "conferencePaper"),
        (5, "annotation"), (6, "note"), (7, "attachment"),
    ]:
        conn.execute("INSERT INTO itemTypes VALUES (?,?,NULL,1)", (tid, name))

    # Fields
    field_defs = [
        (1, "title"), (2, "date"), (3, "DOI"), (4, "url"), (5, "abstractNote"),
        (6, "publicationTitle"), (7, "volume"), (8, "pages"),
    ]
    for fid, fname in field_defs:
        conn.execute("INSERT INTO fields VALUES (?,?,NULL)", (fid, fname))
        conn.execute("INSERT INTO fieldsCombined VALUES (?,?)", (fid, fname))

    # Items: 10 regular items
    items_data = [
        (1, 2, 1, "AAAA0001"),  # journalArticle
        (2, 2, 1, "AAAA0002"),
        (3, 2, 1, "AAAA0003"),
        (4, 2, 1, "AAAA0004"),
        (5, 3, 1, "AAAA0005"),  # book
        (6, 3, 1, "AAAA0006"),
        (7, 4, 1, "AAAA0007"),  # conferencePaper
        (8, 4, 1, "AAAA0008"),
        (9, 2, 1, "AAAA0009"),
        (10, 2, 1, "AAAA0010"),
    ]
    conn.executemany(
        "INSERT INTO items (itemID, itemTypeID, libraryID, key) VALUES (?,?,?,?)",
        items_data,
    )

    # Items that should be EXCLUDED by filtering:
    # Group library items (libraryID=2)
    group_items = [
        (101, 2, 2, "GRP00001"),  # journalArticle in group library
        (102, 3, 2, "GRP00002"),  # book in group library
    ]
    conn.executemany(
        "INSERT INTO items (itemID, itemTypeID, libraryID, key) VALUES (?,?,?,?)",
        group_items,
    )

    # Feed library items (libraryID=3)
    feed_items = [
        (201, 2, 3, "FEED0001"),  # journalArticle in feed library
        (202, 2, 3, "FEED0002"),
    ]
    conn.executemany(
        "INSERT INTO items (itemID, itemTypeID, libraryID, key) VALUES (?,?,?,?)",
        feed_items,
    )

    # Non-bibliographic items in primary library (should be excluded by type)
    nonbib_items = [
        (301, 5, 1, "ANN00001"),  # annotation in primary library
        (302, 6, 1, "NOTE0001"),  # note in primary library
        (303, 7, 1, "ATT00001"),  # attachment type in primary library
    ]
    conn.executemany(
        "INSERT INTO items (itemID, itemTypeID, libraryID, key) VALUES (?,?,?,?)",
        nonbib_items,
    )

    # Group library collection (should be excluded)
    conn.execute(
        "INSERT INTO collections VALUES (10,'Group Papers',NULL,'2024-01-01',2,'GCOL0001',0,0)"
    )

    # Item data values (titles and dates)
    # itemDataValues.value has a UNIQUE constraint, so shared values must reuse
    # the same valueID.  Insert with INSERT OR IGNORE and look up the rowid.
    value_id = 1
    item_values: list[tuple] = []
    for iid in range(1, 11):
        # title — unique per item
        conn.execute("INSERT OR IGNORE INTO itemDataValues VALUES (?,?)", (value_id, f"Test Item {iid}"))
        item_values.append((iid, 1, value_id))
        value_id += 1
        # date — unique per item (use full ISO year-month so no collisions)
        date_val = f"2020-{iid:02d}-01"
        conn.execute("INSERT OR IGNORE INTO itemDataValues VALUES (?,?)", (value_id, date_val))
        item_values.append((iid, 2, value_id))
        value_id += 1
    conn.executemany("INSERT INTO itemData VALUES (?,?,?)", item_values)

    # Creators
    conn.execute("INSERT INTO creators VALUES (1,'Ada','Lovelace',0)")
    conn.execute("INSERT INTO creators VALUES (2,'Charles','Babbage',0)")
    for iid in range(1, 11):
        conn.execute(
            "INSERT INTO itemCreators VALUES (?,1,1,0)", (iid,)
        )

    # Collections: 3 collections, col3 is child of col1
    conn.execute("INSERT INTO collections VALUES (1,'Neuroscience',NULL,'2024-01-01',1,'COL00001',0,0)")
    conn.execute("INSERT INTO collections VALUES (2,'Machine Learning',NULL,'2024-01-01',1,'COL00002',0,0)")
    conn.execute("INSERT INTO collections VALUES (3,'Deep Learning',1,'2024-01-01',1,'COL00003',0,0)")

    # Collection-item assignments: items 1-4 in col1, items 5-7 in col2, items 8-10 in col3
    for iid in range(1, 5):
        conn.execute("INSERT INTO collectionItems VALUES (1,?,0)", (iid,))
    for iid in range(5, 8):
        conn.execute("INSERT INTO collectionItems VALUES (2,?,0)", (iid,))
    for iid in range(8, 11):
        conn.execute("INSERT INTO collectionItems VALUES (3,?,0)", (iid,))

    # Tags: 15 tags
    tag_names = [
        "neuron", "neural network", "deep learning", "spiking",
        "cortex", "simulation", "review", "unread", "important",
        "Python", "machine learning", "attention mechanism",
        "basal ganglia", "plasticity", "theoretical",
    ]
    for tid, name in enumerate(tag_names, start=1):
        conn.execute("INSERT INTO tags VALUES (?,?)", (tid, name))

    # Item-tag assignments (each item gets ~3 tags)
    assignments = [
        (1, 1), (1, 4), (1, 8),   # item1: neuron, spiking, unread
        (2, 1), (2, 5), (2, 9),   # item2: neuron, cortex, important
        (3, 2), (3, 6), (3, 7),   # item3: neural network, simulation, review
        (4, 1), (4, 14), (4, 15), # item4: neuron, plasticity, theoretical
        (5, 3), (5, 10), (5, 11), # item5: deep learning, Python, machine learning
        (6, 2), (6, 11), (6, 12), # item6: neural network, machine learning, attention mechanism
        (7, 3), (7, 12), (7, 8),  # item7: deep learning, attention mechanism, unread
        (8, 13), (8, 6), (8, 9),  # item8: basal ganglia, simulation, important
        (9, 5), (9, 14), (9, 7),  # item9: cortex, plasticity, review
        (10, 2), (10, 15), (10, 8), # item10: neural network, theoretical, unread
    ]
    conn.executemany("INSERT INTO itemTags VALUES (?,?,0)", assignments)

    conn.commit()
    conn.close()


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture(scope="session")
def small_zotero_sqlite(tmp_path_factory) -> Path:
    """Temporary Zotero-compatible SQLite file with 10 items, 3 collections, 15 tags."""
    tmp_dir = tmp_path_factory.mktemp("zotero")
    db_path = tmp_dir / "test_zotero.sqlite"
    _build_test_db(db_path)
    return db_path


@pytest.fixture(scope="session")
def sample_library(small_zotero_sqlite) -> Library:
    """Library object constructed from the test fixture database."""
    raw = read_library(small_zotero_sqlite)
    return Library.from_raw(raw)


@pytest.fixture
def sim_db_path(tmp_path) -> Path:
    """
    Temporary path for a simulation database (unique per test).

    Uses project DATA_DIR so writes pass the security whitelist.
    The file is unique per test by embedding the tmp_path stem.
    Cleans up after itself.
    """
    from zotero_restructuring.config import DATA_DIR
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    db_path = DATA_DIR / f"test_{tmp_path.name}.sqlite"
    yield db_path
    if db_path.exists():
        db_path.unlink()
