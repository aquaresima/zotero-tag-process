"""
models.py — SQLAlchemy ORM models for the simulation database.

These models define the writable simulation.sqlite schema; they do NOT
mirror the source Zotero schema exactly but represent the same logical
entities in a way that supports CRUD and diff tracking.
"""

from __future__ import annotations

import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    event,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class SimCollection(Base):
    """A Zotero collection (folder) in the simulation database."""

    __tablename__ = "sim_collections"

    collection_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    key: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    parent_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("sim_collections.collection_id", ondelete="SET NULL"),
        nullable=True, default=None
    )

    # Audit / change tracking
    original_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    modified: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # Relationships
    items: Mapped[list[SimCollectionItem]] = relationship(
        "SimCollectionItem", back_populates="collection", cascade="all, delete-orphan"
    )
    # Self-referential: parent -> children
    # "remote_side" marks collection_id as the "one" side so SQLAlchemy knows
    # parent_id is the foreign key pointing UP to the parent.
    children: Mapped[list[SimCollection]] = relationship(
        "SimCollection",
        foreign_keys="[SimCollection.parent_id]",
        back_populates="parent_collection",
        lazy="select",
    )
    parent_collection: Mapped[SimCollection | None] = relationship(
        "SimCollection",
        foreign_keys="[SimCollection.parent_id]",
        back_populates="children",
        remote_side="[SimCollection.collection_id]",
        lazy="select",
    )

    def __repr__(self) -> str:
        return f"<SimCollection id={self.collection_id} name={self.name!r}>"


class SimItem(Base):
    """A Zotero library item in the simulation database."""

    __tablename__ = "sim_items"

    item_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    key: Mapped[str] = mapped_column(String(64), nullable=False)
    item_type: Mapped[str] = mapped_column(String(64), nullable=False)
    title: Mapped[str | None] = mapped_column(Text, nullable=True)
    date: Mapped[str | None] = mapped_column(String(64), nullable=True)
    doi: Mapped[str | None] = mapped_column(String(256), nullable=True)
    url: Mapped[str | None] = mapped_column(Text, nullable=True)
    creators_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    metadata_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    date_added: Mapped[str | None] = mapped_column(String(64), nullable=True)
    citation_count: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Relationships
    tags: Mapped[list[SimItemTag]] = relationship(
        "SimItemTag", back_populates="item", cascade="all, delete-orphan"
    )
    collections: Mapped[list[SimCollectionItem]] = relationship(
        "SimCollectionItem", back_populates="item", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<SimItem id={self.item_id} key={self.key!r} type={self.item_type!r}>"


class SimTag(Base):
    """A tag in the simulation database."""

    __tablename__ = "sim_tags"

    tag_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    normalized_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    category: Mapped[str | None] = mapped_column(String(64), nullable=True)

    # Audit
    original_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    modified: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_merge_candidate: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False
    )
    merge_target_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("sim_tags.tag_id", ondelete="SET NULL"),
        nullable=True, default=None
    )

    # Relationships
    items: Mapped[list[SimItemTag]] = relationship(
        "SimItemTag", back_populates="tag", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<SimTag id={self.tag_id} name={self.name!r}>"


class SimItemTag(Base):
    """Many-to-many: item <-> tag."""

    __tablename__ = "sim_item_tags"
    __table_args__ = (UniqueConstraint("item_id", "tag_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    item_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("sim_items.item_id", ondelete="CASCADE"), nullable=False
    )
    tag_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("sim_tags.tag_id", ondelete="CASCADE"), nullable=False
    )
    tag_type: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    item: Mapped[SimItem] = relationship("SimItem", back_populates="tags")
    tag: Mapped[SimTag] = relationship("SimTag", back_populates="items")


class SimCollectionItem(Base):
    """Many-to-many: collection <-> item."""

    __tablename__ = "sim_collection_items"
    __table_args__ = (UniqueConstraint("collection_id", "item_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    collection_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("sim_collections.collection_id", ondelete="CASCADE"),
        nullable=False
    )
    item_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("sim_items.item_id", ondelete="CASCADE"), nullable=False
    )
    order_index: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    collection: Mapped[SimCollection] = relationship(
        "SimCollection", back_populates="items"
    )
    item: Mapped[SimItem] = relationship("SimItem", back_populates="collections")


class TagProposal(Base):
    """A proposed tag for an item, produced by the propagation worker.

    Status lifecycle:
        pending -> approved | rejected | edited -> applied (after Phase 4)
    The worker regenerates ``pending`` rows on each run but never touches
    rows already in ``approved``/``rejected``/``edited`` state.
    """

    __tablename__ = "tag_proposals"
    __table_args__ = (UniqueConstraint("item_id", "tag_name"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    item_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("sim_items.item_id", ondelete="CASCADE"), nullable=False
    )
    tag_name: Mapped[str] = mapped_column(Text, nullable=False)
    confidence: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    # JSON list of Zotero item keys of the neighbors that contributed this tag.
    source_item_keys: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="pending", nullable=False)
    edited_tag: Mapped[str | None] = mapped_column(Text, nullable=True)
    low_confidence: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False
    )
    # True when tag_name did not exist in sim_tags at proposal time (Haiku invented it)
    is_new_tag: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    # "haiku_batch" | "neighbor_vote"
    generated_by: Mapped[str] = mapped_column(
        String(32), default="neighbor_vote", nullable=False
    )
    category: Mapped[str] = mapped_column(
        String(16), default="general", nullable=False
    )
    created_at: Mapped[str] = mapped_column(
        String(32),
        default=lambda: datetime.datetime.now(datetime.timezone.utc).isoformat(),
        nullable=False,
    )
    applied_at: Mapped[str | None] = mapped_column(String(32), nullable=True)

    item: Mapped[SimItem] = relationship("SimItem")

    @property
    def effective_tag(self) -> str:
        """Return the tag that would actually be applied (edit overrides name)."""
        return self.edited_tag or self.tag_name

    def __repr__(self) -> str:
        return (
            f"<TagProposal id={self.id} item={self.item_id} "
            f"tag={self.tag_name!r} conf={self.confidence:.2f} status={self.status}>"
        )


class SessionMeta(Base):
    """Session provenance: records what data and parameters produced this DB.

    Exactly one row is expected per ``zotero_restructure.db``.  Makes every
    simulation database self-documenting.
    """

    __tablename__ = "meta"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    zotero_sqlite_path: Mapped[str] = mapped_column(Text, nullable=False)
    chroma_db_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    chroma_collection: Mapped[str | None] = mapped_column(Text, nullable=True)
    embedding_model: Mapped[str | None] = mapped_column(Text, nullable=True)
    # JSON blob: {threshold, top_k, ...}
    worker_params: Mapped[str | None] = mapped_column(Text, nullable=True)
    import_timestamp: Mapped[str] = mapped_column(
        String(32),
        default=lambda: datetime.datetime.now(datetime.timezone.utc).isoformat(),
        nullable=False,
    )
    worker_timestamp: Mapped[str | None] = mapped_column(String(32), nullable=True)
    library_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    item_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    tag_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    def __repr__(self) -> str:
        return (
            f"<SessionMeta id={self.id} items={self.item_count} "
            f"tags={self.tag_count} model={self.embedding_model!r}>"
        )


class ExcludedTag(Base):
    """Tags that must not be counted as substantive when determining if an item needs proposals.

    Pre-populated at import time from TAG_PROTECTED, TAG_STATUS_VALUES, TAG_QUALITY_VALUES.
    User can add/remove entries via the Stats page before running the worker.
    """

    __tablename__ = "excluded_tags"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    reason: Mapped[str] = mapped_column(
        String(32), nullable=False, default="user"
    )  # "status" | "quality" | "protected" | "junk_pattern" | "user"

    def __repr__(self) -> str:
        return f"<ExcludedTag {self.name!r} ({self.reason})>"


class CitationInbox(Base):
    """Papers not found by Semantic Scholar citation count lookup.

    User can manually enter citation counts here for papers the API couldn't find.
    """

    __tablename__ = "citation_inbox"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    item_id: Mapped[int] = mapped_column(Integer, nullable=False)
    title: Mapped[str | None] = mapped_column(Text, nullable=True)
    doi: Mapped[str | None] = mapped_column(Text, nullable=True)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[str] = mapped_column(String(32), nullable=False)

    def __repr__(self) -> str:
        return f"<CitationInbox id={self.id} item_id={self.item_id}>"


class ChangeLog(Base):
    """Audit log of every mutation applied to the simulation database."""

    __tablename__ = "changelog"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    timestamp: Mapped[str] = mapped_column(
        String(32),
        default=lambda: datetime.datetime.now(datetime.timezone.utc).isoformat(),
        nullable=False,
    )
    entity_type: Mapped[str] = mapped_column(String(32), nullable=False)
    entity_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    operation: Mapped[str] = mapped_column(String(32), nullable=False)
    field_name: Mapped[str | None] = mapped_column(String(64), nullable=True)
    old_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    new_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    def __repr__(self) -> str:
        return (
            f"<ChangeLog id={self.id} op={self.operation!r} "
            f"entity={self.entity_type}/{self.entity_id}>"
        )
