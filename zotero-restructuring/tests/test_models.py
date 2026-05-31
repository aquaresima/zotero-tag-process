"""
test_models.py — Tests for models.py (SQLAlchemy ORM).

Covers:
- Model instantiation and persistence
- Relationship traversal
- ChangeLog creation
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from zotero_restructuring.models import (
    Base,
    ChangeLog,
    SimCollection,
    SimCollectionItem,
    SimItem,
    SimItemTag,
    SimTag,
)


@pytest.fixture
def in_memory_session():
    """SQLAlchemy session backed by an in-memory SQLite database."""
    engine = create_engine("sqlite:///:memory:", echo=False)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    sess = Session()
    yield sess
    sess.close()
    engine.dispose()


class TestSimCollection:
    def test_create_collection(self, in_memory_session):
        col = SimCollection(collection_id=1, key="K1", name="NeuroSci")
        in_memory_session.add(col)
        in_memory_session.commit()
        fetched = in_memory_session.get(SimCollection, 1)
        assert fetched.name == "NeuroSci"
        assert fetched.key == "K1"
        assert fetched.modified is False

    def test_collection_parent_child(self, in_memory_session):
        parent = SimCollection(collection_id=1, key="P1", name="Parent")
        child = SimCollection(collection_id=2, key="C1", name="Child", parent_id=1)
        in_memory_session.add_all([parent, child])
        in_memory_session.commit()
        fetched_child = in_memory_session.get(SimCollection, 2)
        assert fetched_child.parent_id == 1


class TestSimItem:
    def test_create_item(self, in_memory_session):
        item = SimItem(
            item_id=10, key="ITEM01", item_type="journalArticle",
            title="Test Paper", date="2023",
        )
        in_memory_session.add(item)
        in_memory_session.commit()
        fetched = in_memory_session.get(SimItem, 10)
        assert fetched.title == "Test Paper"
        assert fetched.item_type == "journalArticle"


class TestSimTag:
    def test_create_tag(self, in_memory_session):
        tag = SimTag(tag_id=5, name="neuron")
        in_memory_session.add(tag)
        in_memory_session.commit()
        fetched = in_memory_session.get(SimTag, 5)
        assert fetched.name == "neuron"
        assert fetched.is_merge_candidate is False

    def test_tag_merge_candidate_flag(self, in_memory_session):
        tag = SimTag(tag_id=6, name="neurons", is_merge_candidate=True)
        in_memory_session.add(tag)
        in_memory_session.commit()
        fetched = in_memory_session.get(SimTag, 6)
        assert fetched.is_merge_candidate is True


class TestRelationships:
    def test_item_tag_relationship(self, in_memory_session):
        item = SimItem(item_id=1, key="I1", item_type="book", title="A Book")
        tag = SimTag(tag_id=1, name="review")
        link = SimItemTag(item_id=1, tag_id=1)
        in_memory_session.add_all([item, tag, link])
        in_memory_session.commit()

        fetched_item = in_memory_session.get(SimItem, 1)
        assert len(fetched_item.tags) == 1
        assert fetched_item.tags[0].tag.name == "review"

    def test_collection_item_relationship(self, in_memory_session):
        col = SimCollection(collection_id=1, key="C1", name="Col")
        item = SimItem(item_id=1, key="I1", item_type="book", title="A Book")
        link = SimCollectionItem(collection_id=1, item_id=1)
        in_memory_session.add_all([col, item, link])
        in_memory_session.commit()

        fetched_col = in_memory_session.get(SimCollection, 1)
        assert len(fetched_col.items) == 1


class TestChangeLog:
    def test_create_changelog_entry(self, in_memory_session):
        entry = ChangeLog(
            entity_type="collection",
            entity_id=1,
            operation="rename",
            field_name="name",
            old_value="Old",
            new_value="New",
            description="Renamed Old -> New",
        )
        in_memory_session.add(entry)
        in_memory_session.commit()
        fetched = in_memory_session.get(ChangeLog, 1)
        assert fetched.operation == "rename"
        assert fetched.old_value == "Old"
        assert fetched.new_value == "New"


class TestReprMethods:
    def test_sim_collection_repr(self, in_memory_session):
        col = SimCollection(collection_id=1, key="K1", name="NeuroSci")
        in_memory_session.add(col)
        in_memory_session.commit()
        fetched = in_memory_session.get(SimCollection, 1)
        r = repr(fetched)
        assert "SimCollection" in r
        assert "NeuroSci" in r

    def test_sim_item_repr(self, in_memory_session):
        item = SimItem(
            item_id=1, key="I1", item_type="journalArticle", title="Paper"
        )
        in_memory_session.add(item)
        in_memory_session.commit()
        fetched = in_memory_session.get(SimItem, 1)
        r = repr(fetched)
        assert "SimItem" in r
        assert "I1" in r

    def test_sim_tag_repr(self, in_memory_session):
        tag = SimTag(tag_id=1, name="neuron")
        in_memory_session.add(tag)
        in_memory_session.commit()
        fetched = in_memory_session.get(SimTag, 1)
        r = repr(fetched)
        assert "SimTag" in r
        assert "neuron" in r

    def test_changelog_repr(self, in_memory_session):
        entry = ChangeLog(
            entity_type="tag", entity_id=5, operation="rename"
        )
        in_memory_session.add(entry)
        in_memory_session.commit()
        fetched = in_memory_session.get(ChangeLog, 1)
        r = repr(fetched)
        assert "ChangeLog" in r
        assert "rename" in r
