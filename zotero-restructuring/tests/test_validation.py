"""
test_validation.py — Tests for validation.py.

Covers:
- Happy path: clean library passes without errors
- Orphaned items: warning but not error
- Collection hierarchy cycles: raises ValidationError
- Dangling tag references: warning
- Empty collections: warning
"""

from __future__ import annotations

import pytest

from zotero_restructuring.library import Collection, Item, Library, Tag
from zotero_restructuring.reader import RawLibraryData
from zotero_restructuring.validation import ValidationError, validate


def build_clean_library() -> Library:
    """A fully consistent 3-item, 2-collection, 2-tag library."""
    raw = RawLibraryData(
        items={
            1: {"item_id": 1, "key": "K1", "item_type": "journalArticle",
                "title": "A", "creators": [], "date": None,
                "doi": None, "url": None, "date_added": None, "metadata": {}},
            2: {"item_id": 2, "key": "K2", "item_type": "book",
                "title": "B", "creators": [], "date": None,
                "doi": None, "url": None, "date_added": None, "metadata": {}},
        },
        collections={
            10: {"collection_id": 10, "key": "C1", "name": "ColA", "parent_id": None},
            20: {"collection_id": 20, "key": "C2", "name": "ColB", "parent_id": 10},
        },
        tags={
            100: {"tag_id": 100, "name": "neuron"},
        },
        item_tags=[{"item_id": 1, "tag_id": 100}],
        collection_items=[
            {"collection_id": 10, "item_id": 1},
            {"collection_id": 20, "item_id": 2},
        ],
    )
    return Library.from_raw(raw)


class TestHappyPath:
    def test_clean_library_no_errors(self):
        lib = build_clean_library()
        report = validate(lib)
        assert report.orphaned_item_keys == []
        assert report.dangling_tag_refs == []

    def test_clean_library_from_fixture(self, sample_library):
        report = validate(sample_library)
        # No cycles; orphans and empty collections are acceptable warnings
        assert report.dangling_tag_refs == []
        assert report.dangling_collection_refs == []


class TestOrphanedItems:
    def test_orphaned_item_is_warning(self):
        """Orphaned items produce a warning, not a ValidationError."""
        raw = RawLibraryData(
            items={
                1: {"item_id": 1, "key": "K1", "item_type": "journalArticle",
                    "title": "Orphan", "creators": [], "date": None,
                    "doi": None, "url": None, "date_added": None, "metadata": {}},
            },
            collections={
                10: {"collection_id": 10, "key": "C1", "name": "Col", "parent_id": None},
            },
            tags={},
            item_tags=[],
            collection_items=[],  # item 1 not in any collection
        )
        lib = Library.from_raw(raw)
        report = validate(lib)
        assert "K1" in report.orphaned_item_keys
        assert any("orphan" in w.lower() for w in report.warnings)

    def test_orphaned_item_keys_included(self):
        raw = RawLibraryData(
            items={
                1: {"item_id": 1, "key": "ORPHANKEY", "item_type": "book",
                    "title": "X", "creators": [], "date": None,
                    "doi": None, "url": None, "date_added": None, "metadata": {}},
            },
            collections={},
            tags={},
            item_tags=[],
            collection_items=[],
        )
        lib = Library.from_raw(raw)
        report = validate(lib)
        assert "ORPHANKEY" in report.orphaned_item_keys


class TestCollectionCycles:
    def test_cycle_raises_validation_error(self):
        """Circular parent references must raise ValidationError."""
        # Build a library directly with a cycle: col1.parent=col2, col2.parent=col1
        lib = Library(
            items={},
            collections={
                1: Collection(collection_id=1, key="C1", name="A", parent_id=2),
                2: Collection(collection_id=2, key="C2", name="B", parent_id=1),
            },
            tags={},
        )
        with pytest.raises(ValidationError) as exc_info:
            validate(lib)
        assert "cycle" in str(exc_info.value).lower()

    def test_self_referential_cycle(self):
        """A collection whose parent is itself is a cycle."""
        lib = Library(
            items={},
            collections={
                1: Collection(collection_id=1, key="C1", name="A", parent_id=1),
            },
            tags={},
        )
        with pytest.raises(ValidationError):
            validate(lib)

    def test_deep_hierarchy_no_cycle(self):
        """A 3-level hierarchy without cycles should not raise."""
        lib = Library(
            items={},
            collections={
                1: Collection(collection_id=1, key="C1", name="Root", parent_id=None),
                2: Collection(collection_id=2, key="C2", name="Child", parent_id=1),
                3: Collection(collection_id=3, key="C3", name="GrandChild", parent_id=2),
            },
            tags={},
        )
        report = validate(lib)  # should not raise
        assert report.dangling_tag_refs == []


class TestEmptyCollections:
    def test_empty_collection_warns(self):
        raw = RawLibraryData(
            items={},
            collections={
                1: {"collection_id": 1, "key": "C1", "name": "Empty", "parent_id": None},
            },
            tags={},
            item_tags=[],
            collection_items=[],
        )
        lib = Library.from_raw(raw)
        report = validate(lib)
        assert "Empty" in report.empty_collection_names


class TestDanglingReferences:
    def test_dangling_tag_ref(self):
        """Item references a tag that does not exist in the library."""
        item = Item(item_id=1, key="K1", item_type="book", title="X",
                    creators=[], date=None, doi=None, url=None,
                    tags={999})  # tag 999 does not exist
        lib = Library(
            items={1: item},
            collections={
                10: Collection(collection_id=10, key="C1", name="Col",
                               parent_id=None, items={1}),
            },
            tags={},
        )
        report = validate(lib)
        assert len(report.dangling_tag_refs) == 1
        assert (1, 999) in report.dangling_tag_refs

    def test_dangling_collection_ref(self):
        """Item references a collection that does not exist in the library."""
        item = Item(item_id=1, key="K1", item_type="book", title="X",
                    creators=[], date=None, doi=None, url=None,
                    collections={888})  # collection 888 does not exist
        lib = Library(
            items={1: item},
            collections={},
            tags={},
        )
        report = validate(lib)
        assert len(report.dangling_collection_refs) == 1
        assert (1, 888) in report.dangling_collection_refs


class TestValidationReportSummary:
    def test_ok_summary(self):
        from zotero_restructuring.validation import ValidationReport
        report = ValidationReport()
        s = report.summary()
        assert "OK" in s

    def test_summary_with_orphans(self):
        from zotero_restructuring.validation import ValidationReport
        report = ValidationReport()
        report.orphaned_item_keys = ["K1", "K2", "K3", "K4", "K5", "K6"]
        report.add_warning("6 orphans")
        s = report.summary()
        assert "Orphaned items" in s
        assert "..." in s  # truncated

    def test_summary_with_empty_collections(self):
        from zotero_restructuring.validation import ValidationReport
        report = ValidationReport()
        report.empty_collection_names = ["EmptyCol"]
        report.add_warning("1 empty")
        s = report.summary()
        assert "Empty collections" in s

    def test_summary_with_dangling_tags(self):
        from zotero_restructuring.validation import ValidationReport
        report = ValidationReport()
        report.dangling_tag_refs = [(1, 999)]
        report.add_warning("1 dangling tag")
        s = report.summary()
        assert "Dangling tag refs" in s

    def test_summary_with_dangling_collections(self):
        from zotero_restructuring.validation import ValidationReport
        report = ValidationReport()
        report.dangling_collection_refs = [(1, 888)]
        report.add_warning("1 dangling collection")
        s = report.summary()
        assert "Dangling collection refs" in s
