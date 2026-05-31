"""
test_library.py — Tests for library.py.

Covers:
- Construction from raw data
- Query methods: items_in_collection, items_with_tag, find_items
- orphaned_items detection
- collection_subtree traversal
- root_collections
- stats()
"""

from __future__ import annotations

import pytest

from zotero_restructuring.library import Collection, Item, Library, Tag
from zotero_restructuring.reader import RawLibraryData


def make_minimal_raw() -> RawLibraryData:
    """Build a tiny RawLibraryData for unit testing without SQLite."""
    items = {
        1: {"item_id": 1, "key": "K001", "item_type": "journalArticle",
            "title": "Alpha", "creators": ["Alice"], "date": "2020",
            "doi": "10.1/a", "url": None, "date_added": None, "metadata": {}},
        2: {"item_id": 2, "key": "K002", "item_type": "book",
            "title": "Beta", "creators": [], "date": None,
            "doi": None, "url": None, "date_added": None, "metadata": {}},
        3: {"item_id": 3, "key": "K003", "item_type": "journalArticle",
            "title": "Gamma", "creators": ["Bob"], "date": "2022",
            "doi": None, "url": None, "date_added": None, "metadata": {}},
    }
    collections = {
        10: {"collection_id": 10, "key": "C001", "name": "NeuroSci", "parent_id": None},
        20: {"collection_id": 20, "key": "C002", "name": "ML", "parent_id": None},
        30: {"collection_id": 30, "key": "C003", "name": "Deep", "parent_id": 20},
    }
    tags = {
        100: {"tag_id": 100, "name": "neuron"},
        200: {"tag_id": 200, "name": "review"},
    }
    item_tags = [
        {"item_id": 1, "tag_id": 100},
        {"item_id": 2, "tag_id": 200},
        {"item_id": 3, "tag_id": 100},
        {"item_id": 3, "tag_id": 200},
    ]
    collection_items = [
        {"collection_id": 10, "item_id": 1},
        {"collection_id": 20, "item_id": 2},
        {"collection_id": 30, "item_id": 3},
    ]
    return RawLibraryData(items, collections, tags, item_tags, collection_items)


@pytest.fixture
def minimal_library() -> Library:
    return Library.from_raw(make_minimal_raw())


class TestLibraryConstruction:
    def test_items_count(self, minimal_library):
        assert len(minimal_library.items) == 3

    def test_collections_count(self, minimal_library):
        assert len(minimal_library.collections) == 3

    def test_tags_count(self, minimal_library):
        assert len(minimal_library.tags) == 2

    def test_item_fields(self, minimal_library):
        item = minimal_library.items[1]
        assert item.key == "K001"
        assert item.title == "Alpha"
        assert item.doi == "10.1/a"
        assert item.creators == ["Alice"]

    def test_item_tags_wired(self, minimal_library):
        assert 100 in minimal_library.items[1].tags
        assert 200 in minimal_library.items[3].tags

    def test_tag_items_wired(self, minimal_library):
        neuron = minimal_library.tags[100]
        assert 1 in neuron.items
        assert 3 in neuron.items

    def test_collection_items_wired(self, minimal_library):
        neurosci = minimal_library.collections[10]
        assert 1 in neurosci.items

    def test_item_collections_wired(self, minimal_library):
        assert 10 in minimal_library.items[1].collections

    def test_collection_hierarchy(self, minimal_library):
        ml = minimal_library.collections[20]
        deep = minimal_library.collections[30]
        assert 30 in ml.children
        assert deep.parent_id == 20


class TestQueryMethods:
    def test_items_in_collection(self, minimal_library):
        items = minimal_library.items_in_collection(10)
        assert len(items) == 1
        assert items[0].item_id == 1

    def test_items_in_nonexistent_collection(self, minimal_library):
        assert minimal_library.items_in_collection(9999) == []

    def test_items_with_tag(self, minimal_library):
        items = minimal_library.items_with_tag(100)
        ids = {i.item_id for i in items}
        assert ids == {1, 3}

    def test_items_with_tag_name(self, minimal_library):
        items = minimal_library.items_with_tag_name("NEURON")
        assert len(items) == 2

    def test_find_items(self, minimal_library):
        results = minimal_library.find_items("alpha")
        assert len(results) == 1
        assert results[0].item_id == 1

    def test_find_items_no_match(self, minimal_library):
        assert minimal_library.find_items("zzzzz") == []

    def test_orphaned_items(self, minimal_library):
        """In our minimal data all items are in collections — no orphans."""
        orphans = minimal_library.orphaned_items()
        assert len(orphans) == 0

    def test_orphaned_items_detected(self):
        """An item with no collection membership is an orphan."""
        raw = make_minimal_raw()
        # Remove all collection-item links for item 3
        raw.collection_items = [r for r in raw.collection_items if r["item_id"] != 3]
        lib = Library.from_raw(raw)
        orphans = lib.orphaned_items()
        orphan_ids = {i.item_id for i in orphans}
        assert 3 in orphan_ids

    def test_collection_subtree(self, minimal_library):
        # ml(20) has child deep(30); subtree of ml should contain both
        subtree = minimal_library.collection_subtree(20)
        assert 20 in subtree
        assert 30 in subtree
        assert 10 not in subtree

    def test_root_collections(self, minimal_library):
        roots = minimal_library.root_collections()
        root_ids = {c.collection_id for c in roots}
        assert 10 in root_ids
        assert 20 in root_ids
        assert 30 not in root_ids  # has parent

    def test_tag_by_name(self, minimal_library):
        tag = minimal_library.tag_by_name("review")
        assert tag is not None
        assert tag.tag_id == 200

    def test_tag_by_name_case_insensitive(self, minimal_library):
        tag = minimal_library.tag_by_name("NEURON")
        assert tag is not None

    def test_tag_by_name_not_found(self, minimal_library):
        assert minimal_library.tag_by_name("nonexistent") is None


class TestStats:
    def test_stats(self, minimal_library):
        s = minimal_library.stats()
        assert s["items"] == 3
        assert s["collections"] == 3
        assert s["tags"] == 2

    def test_stats_from_fixture(self, sample_library):
        s = sample_library.stats()
        assert s["items"] == 10
        assert s["collections"] == 3
        assert s["tags"] == 15


class TestDataclassMethods:
    """Test __hash__, __eq__, and __repr__ on domain dataclasses."""

    def test_item_hash(self):
        item = Item(item_id=1, key="K1", item_type="book", title="T",
                    creators=[], date=None, doi=None, url=None)
        assert hash(item) == hash(1)

    def test_item_eq_same(self):
        a = Item(item_id=1, key="K1", item_type="book", title="T",
                 creators=[], date=None, doi=None, url=None)
        b = Item(item_id=1, key="K1", item_type="book", title="T",
                 creators=[], date=None, doi=None, url=None)
        assert a == b

    def test_item_eq_different(self):
        a = Item(item_id=1, key="K1", item_type="book", title="T",
                 creators=[], date=None, doi=None, url=None)
        b = Item(item_id=2, key="K2", item_type="book", title="T",
                 creators=[], date=None, doi=None, url=None)
        assert a != b

    def test_item_eq_not_item(self):
        a = Item(item_id=1, key="K1", item_type="book", title="T",
                 creators=[], date=None, doi=None, url=None)
        assert a != "not an item"

    def test_collection_hash(self):
        col = Collection(collection_id=10, key="C1", name="A", parent_id=None)
        assert hash(col) == hash(10)

    def test_collection_eq_same(self):
        a = Collection(collection_id=10, key="C1", name="A", parent_id=None)
        b = Collection(collection_id=10, key="C1", name="A", parent_id=None)
        assert a == b

    def test_collection_eq_different(self):
        a = Collection(collection_id=10, key="C1", name="A", parent_id=None)
        b = Collection(collection_id=20, key="C2", name="B", parent_id=None)
        assert a != b

    def test_collection_eq_not_collection(self):
        a = Collection(collection_id=10, key="C1", name="A", parent_id=None)
        assert a != 42

    def test_tag_hash(self):
        tag = Tag(tag_id=100, name="neuron")
        assert hash(tag) == hash(100)

    def test_tag_eq_same(self):
        a = Tag(tag_id=100, name="neuron")
        b = Tag(tag_id=100, name="neuron")
        assert a == b

    def test_tag_eq_different(self):
        a = Tag(tag_id=100, name="neuron")
        b = Tag(tag_id=200, name="other")
        assert a != b

    def test_tag_eq_not_tag(self):
        a = Tag(tag_id=100, name="neuron")
        assert a != "neuron"

    def test_library_repr(self, minimal_library):
        r = repr(minimal_library)
        assert "Library" in r
        assert "items=3" in r
        assert "collections=3" in r
        assert "tags=2" in r
