"""
test_reader.py — Tests for reader.py.

Covers:
- Happy path: full ingestion from test fixture
- FileNotFoundError on missing file
- Locked database fallback (simulated)
- Schema mismatch warning (minimal/partial schema)
- Empty database
"""

from __future__ import annotations

import sqlite3
import warnings
from pathlib import Path

import pytest

from zotero_restructuring.reader import SchemaWarning, read_library
from zotero_restructuring.library import Library


# ── Scenario A: full ingestion ────────────────────────────────────────────────

class TestFullIngestion:
    def test_full_ingestion(self, small_zotero_sqlite):
        """Scenario A: 10 items, 3 collections, 15 tags are correctly read."""
        raw = read_library(small_zotero_sqlite)
        lib = Library.from_raw(raw)

        assert len(lib.items) == 10, f"Expected 10 items, got {len(lib.items)}"
        assert len(lib.collections) == 3, f"Expected 3 collections, got {len(lib.collections)}"
        assert len(lib.tags) == 15, f"Expected 15 tags, got {len(lib.tags)}"

    def test_items_have_titles(self, sample_library):
        for item in sample_library.items.values():
            assert item.title is not None
            assert "Test Item" in item.title

    def test_items_have_types(self, sample_library):
        types = {item.item_type for item in sample_library.items.values()}
        assert "journalArticle" in types
        assert "book" in types

    def test_collections_populated(self, sample_library):
        col_names = {c.name for c in sample_library.collections.values()}
        assert "Neuroscience" in col_names
        assert "Machine Learning" in col_names
        assert "Deep Learning" in col_names

    def test_collection_items_wired(self, sample_library):
        neurosci = next(
            c for c in sample_library.collections.values() if c.name == "Neuroscience"
        )
        assert len(neurosci.items) == 4

    def test_collection_hierarchy(self, sample_library):
        """col3 (Deep Learning) should be a child of col1 (Neuroscience)."""
        parent = next(
            c for c in sample_library.collections.values() if c.name == "Neuroscience"
        )
        child = next(
            c for c in sample_library.collections.values() if c.name == "Deep Learning"
        )
        assert child.collection_id in parent.children
        assert child.parent_id == parent.collection_id

    def test_tags_wired_to_items(self, sample_library):
        neuron_tag = sample_library.tag_by_name("neuron")
        assert neuron_tag is not None
        assert len(neuron_tag.items) > 0

    def test_items_reference_tags(self, sample_library):
        item1 = sample_library.items[1]
        assert len(item1.tags) > 0
        for tid in item1.tags:
            assert tid in sample_library.tags

    def test_creators_populated(self, sample_library):
        item1 = sample_library.items[1]
        assert len(item1.creators) >= 1
        assert any("Lovelace" in name or "Ada" in name for name in item1.creators)

    def test_items_queryable_by_collection(self, sample_library):
        ml_col = next(
            c for c in sample_library.collections.values() if c.name == "Machine Learning"
        )
        items = sample_library.items_in_collection(ml_col.collection_id)
        assert len(items) == 3

    def test_items_queryable_by_tag(self, sample_library):
        tag = sample_library.tag_by_name("neuron")
        items = sample_library.items_with_tag(tag.tag_id)
        assert len(items) >= 1


# ── Library filtering ────────────────────────────────────────────────────────

class TestLibraryFiltering:
    """Verify that only primary library (libraryID=1) items and collections are loaded,
    and that non-bibliographic item types are excluded."""

    def test_excludes_group_library_items(self, small_zotero_sqlite):
        """Items from group libraries (libraryID=2) must not appear."""
        raw = read_library(small_zotero_sqlite)
        # Group items have IDs 101, 102
        assert 101 not in raw.items
        assert 102 not in raw.items

    def test_excludes_feed_library_items(self, small_zotero_sqlite):
        """Items from feed libraries (libraryID=3) must not appear."""
        raw = read_library(small_zotero_sqlite)
        assert 201 not in raw.items
        assert 202 not in raw.items

    def test_excludes_annotation_items(self, small_zotero_sqlite):
        """Annotation items (itemTypeID for 'annotation') in primary library must not appear."""
        raw = read_library(small_zotero_sqlite)
        assert 301 not in raw.items

    def test_excludes_note_items(self, small_zotero_sqlite):
        """Note items (itemTypeID for 'note') in primary library must not appear."""
        raw = read_library(small_zotero_sqlite)
        assert 302 not in raw.items

    def test_excludes_attachment_type_items(self, small_zotero_sqlite):
        """Items with itemType='attachment' in primary library must not appear."""
        raw = read_library(small_zotero_sqlite)
        assert 303 not in raw.items

    def test_excludes_group_library_collections(self, small_zotero_sqlite):
        """Collections from group libraries must not appear."""
        raw = read_library(small_zotero_sqlite)
        # Group collection has ID 10
        assert 10 not in raw.collections

    def test_only_primary_items_remain(self, small_zotero_sqlite):
        """After filtering, exactly the 10 primary bibliographic items remain."""
        raw = read_library(small_zotero_sqlite)
        lib = Library.from_raw(raw)
        assert len(lib.items) == 10

    def test_only_primary_collections_remain(self, small_zotero_sqlite):
        """After filtering, exactly the 3 primary collections remain."""
        raw = read_library(small_zotero_sqlite)
        lib = Library.from_raw(raw)
        assert len(lib.collections) == 3

    def test_all_remaining_items_are_bibliographic(self, small_zotero_sqlite):
        """Every item that passes filtering should be a real bibliographic type."""
        raw = read_library(small_zotero_sqlite)
        lib = Library.from_raw(raw)
        excluded_types = {"annotation", "note", "attachment"}
        for item in lib.items.values():
            assert item.item_type not in excluded_types, (
                f"Item {item.item_id} has excluded type '{item.item_type}'"
            )


# ── Failure modes ──────────────────────────────────────────────────────────────

class TestFailureModes:
    def test_file_not_found(self, tmp_path):
        """Raise FileNotFoundError with path info when file is missing."""
        missing = tmp_path / "nonexistent.sqlite"
        with pytest.raises(FileNotFoundError) as exc_info:
            read_library(missing)
        assert "ZOTERO_SQLITE_PATH" in str(exc_info.value)
        assert str(missing) in str(exc_info.value)

    def test_wrong_extension(self, tmp_path):
        """Raise ValueError when file does not have .sqlite extension."""
        bad = tmp_path / "zotero.db"
        bad.write_bytes(b"")
        with pytest.raises(ValueError):
            read_library(bad)

    def test_schema_mismatch_warning(self, tmp_path):
        """Emit SchemaWarning for a database missing expected tables."""
        minimal = tmp_path / "minimal.sqlite"
        conn = sqlite3.connect(str(minimal))
        conn.execute("CREATE TABLE items (itemID INTEGER PRIMARY KEY, key TEXT)")
        conn.commit()
        conn.close()

        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            raw = read_library(minimal)

        schema_warns = [w for w in caught if issubclass(w.category, SchemaWarning)]
        assert len(schema_warns) > 0, "Expected SchemaWarning for partial schema"

    def test_empty_database(self, tmp_path):
        """Succeed with empty state for a valid but empty Zotero-schema DB."""
        empty = tmp_path / "empty.sqlite"
        conn = sqlite3.connect(str(empty))
        # Create all expected tables but insert nothing
        conn.executescript("""
            CREATE TABLE libraries (libraryID INTEGER PRIMARY KEY, type TEXT NOT NULL,
                editable INT NOT NULL DEFAULT 1, filesEditable INT NOT NULL DEFAULT 1,
                version INT NOT NULL DEFAULT 0, storageVersion INT NOT NULL DEFAULT 0,
                lastSync INT NOT NULL DEFAULT 0, archived INT NOT NULL DEFAULT 0);
            CREATE TABLE itemTypes (itemTypeID INTEGER PRIMARY KEY, typeName TEXT,
                templateItemTypeID INT, display INT DEFAULT 1);
            CREATE TABLE fields (fieldID INTEGER PRIMARY KEY, fieldName TEXT, fieldFormatID INT);
            CREATE TABLE fieldsCombined (fieldID INTEGER PRIMARY KEY, fieldName TEXT);
            CREATE TABLE items (itemID INTEGER PRIMARY KEY, itemTypeID INT NOT NULL,
                dateAdded TIMESTAMP, dateModified TIMESTAMP, clientDateModified TIMESTAMP,
                libraryID INT NOT NULL, key TEXT NOT NULL, version INT DEFAULT 0, synced INT DEFAULT 0);
            CREATE TABLE itemDataValues (valueID INTEGER PRIMARY KEY, value UNIQUE);
            CREATE TABLE itemData (itemID INT, fieldID INT, valueID INT, PRIMARY KEY (itemID, fieldID));
            CREATE TABLE creators (creatorID INTEGER PRIMARY KEY, firstName TEXT, lastName TEXT, fieldMode INT DEFAULT 0);
            CREATE TABLE itemCreators (itemID INT NOT NULL, creatorID INT NOT NULL,
                creatorTypeID INT NOT NULL DEFAULT 1, orderIndex INT NOT NULL DEFAULT 0,
                PRIMARY KEY (itemID, creatorID, creatorTypeID, orderIndex), UNIQUE (itemID, orderIndex));
            CREATE TABLE collections (collectionID INTEGER PRIMARY KEY, collectionName TEXT NOT NULL,
                parentCollectionID INT DEFAULT NULL, clientDateModified TIMESTAMP,
                libraryID INT NOT NULL DEFAULT 1, key TEXT NOT NULL, version INT DEFAULT 0, synced INT DEFAULT 0);
            CREATE TABLE collectionItems (collectionID INT NOT NULL, itemID INT NOT NULL,
                orderIndex INT NOT NULL DEFAULT 0, PRIMARY KEY (collectionID, itemID));
            CREATE TABLE tags (tagID INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE);
            CREATE TABLE itemTags (itemID INT NOT NULL, tagID INT NOT NULL, type INT NOT NULL DEFAULT 0,
                PRIMARY KEY (itemID, tagID));
            CREATE TABLE itemAttachments (itemID INTEGER PRIMARY KEY, parentItemID INT,
                linkMode INT, contentType TEXT, charsetID INT, path TEXT);
            CREATE TABLE deletedItems (itemID INTEGER PRIMARY KEY, dateDeleted DEFAULT CURRENT_TIMESTAMP NOT NULL);
        """)
        conn.commit()
        conn.close()

        raw = read_library(empty)
        lib = Library.from_raw(raw)
        assert len(lib.items) == 0
        assert len(lib.collections) == 0
        assert len(lib.tags) == 0

    def test_locked_database_fallback(self, tmp_path, small_zotero_sqlite):
        """Locked file fallback: copy strategy should succeed if ro URI fails."""
        # We can't truly lock a SQLite file in-process, but we can verify
        # the fallback code path by patching sqlite3.connect to fail on uri.
        import sqlite3 as _sqlite3
        original_connect = _sqlite3.connect
        call_count = {"n": 0}

        def patched_connect(database, *args, **kwargs):
            if kwargs.get("uri") and call_count["n"] == 0:
                call_count["n"] += 1
                raise _sqlite3.OperationalError("database is locked")
            return original_connect(database, *args, **kwargs)

        import zotero_restructuring.reader as reader_mod
        original = reader_mod.sqlite3.connect
        reader_mod.sqlite3.connect = patched_connect
        try:
            raw = read_library(small_zotero_sqlite)
            lib = Library.from_raw(raw)
            assert len(lib.items) == 10
        finally:
            reader_mod.sqlite3.connect = original
