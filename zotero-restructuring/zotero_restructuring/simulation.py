"""
simulation.py — Clone library into a simulation SQLite database.

Supports CRUD operations with full change logging and diff generation.
All writes are validated against the path whitelist in config.py.

Failure modes handled:
- Write failure (disk full, permissions): raises IOError, never leaves partial DB
- Rollback: delete simulation DB and start fresh
"""

from __future__ import annotations

import datetime
import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from sqlalchemy import create_engine, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from .config import (
    CHROMA_COLLECTION,
    EMBEDDING_MODEL,
    SIMULATION_DB_PATH,
    TAG_PROTECTED,
    TAG_QUALITY_VALUES,
    TAG_STATUS_VALUES,
    get_chroma_db_path,
    get_zotero_sqlite_path,
    validate_write_path,
)
from .models import (
    Base,
    ChangeLog,
    CitationInbox,
    ExcludedTag,
    SessionMeta,
    SimCollection,
    SimCollectionItem,
    SimItem,
    SimItemTag,
    SimTag,
)

if TYPE_CHECKING:
    from .library import Library


# ── Change record ──────────────────────────────────────────────────────────────

@dataclass
class Change:
    entity_type: str
    entity_id: int | None
    operation: str
    field_name: str | None = None
    old_value: str | None = None
    new_value: str | None = None
    description: str | None = None


@dataclass
class ChangeSet:
    changes: list[Change] = field(default_factory=list)

    def __len__(self) -> int:
        return len(self.changes)

    def __iter__(self):
        return iter(self.changes)

    def summary(self) -> str:
        lines = [f"ChangeSet: {len(self.changes)} change(s)"]
        for c in self.changes:
            desc = c.description or f"{c.operation} {c.entity_type}/{c.entity_id}"
            lines.append(f"  [{c.operation}] {desc}")
        return "\n".join(lines)


# ── SimulationDB ───────────────────────────────────────────────────────────────

class SimulationDB:
    """
    Writable simulation database cloned from a Library.

    All mutations are logged to the ChangeLog table; diff() replays the log
    to produce a ChangeSet.
    """

    def __init__(self, db_path: Path = SIMULATION_DB_PATH) -> None:
        validate_write_path(db_path)
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._path = db_path
        url = f"sqlite:///{db_path}"
        try:
            self._engine = create_engine(url, echo=False)
            Base.metadata.create_all(self._engine)
            self._migrate_schema()
        except SQLAlchemyError as exc:
            raise IOError(
                f"Failed to create simulation database at '{db_path}': {exc}"
            ) from exc
        self._Session = sessionmaker(bind=self._engine)

    def _migrate_schema(self) -> None:
        """Apply safe incremental migrations for columns added after initial DB creation."""
        stmts = [
            "ALTER TABLE sim_items ADD COLUMN citation_count INTEGER",
            "ALTER TABLE tag_proposals ADD COLUMN category VARCHAR(16) NOT NULL DEFAULT 'general'",
            "ALTER TABLE tag_proposals ADD COLUMN is_new_tag BOOLEAN NOT NULL DEFAULT 0",
            "ALTER TABLE tag_proposals ADD COLUMN generated_by TEXT NOT NULL DEFAULT 'haiku_batch'",
            (
                "CREATE TABLE IF NOT EXISTS citation_inbox ("
                "id INTEGER PRIMARY KEY AUTOINCREMENT, "
                "item_id INTEGER NOT NULL, "
                "title TEXT, doi TEXT, reason TEXT, "
                "created_at VARCHAR(32) NOT NULL)"
            ),
        ]
        with self._engine.connect() as conn:
            for stmt in stmts:
                try:
                    conn.execute(text(stmt))
                except Exception:
                    pass
            conn.commit()

    def session(self) -> Session:
        return self._Session()

    # ── Clone ─────────────────────────────────────────────────────────────────

    def clone(
        self,
        library: "Library",
        *,
        zotero_sqlite_path: Path | str | None = None,
        chroma_db_path: Path | str | None = None,
        chroma_collection: str = CHROMA_COLLECTION,
        embedding_model: str = EMBEDDING_MODEL,
        library_version: int | None = None,
    ) -> None:
        """Populate simulation DB from a Library.  Idempotent (clears existing data).

        A single :class:`SessionMeta` row is written after the clone, recording
        the provenance of the source data and the parameters that will be used
        by the tag-propagation worker.  ``chroma_db_path`` and
        ``embedding_model`` default to the values in ``config.py``.
        """
        from .models import TagProposal

        if zotero_sqlite_path is None:
            zotero_sqlite_path = get_zotero_sqlite_path()
        if chroma_db_path is None:
            chroma_db_path = get_chroma_db_path()

        try:
            with self.session() as sess:
                # Clear existing data (full re-import resets everything,
                # including any human decisions, since the source changed).
                for model in (
                    TagProposal, SimItemTag, SimCollectionItem,
                    SimTag, SimItem, SimCollection, ChangeLog, SessionMeta,
                    ExcludedTag,
                ):
                    sess.query(model).delete()
                sess.commit()

                # Insert collections
                for col in library.collections.values():
                    sess.add(SimCollection(
                        collection_id=col.collection_id,
                        key=col.key,
                        name=col.name,
                        parent_id=col.parent_id,
                        original_name=col.name,
                        modified=False,
                    ))
                sess.flush()

                # Insert tags
                for tag in library.tags.values():
                    sess.add(SimTag(
                        tag_id=tag.tag_id,
                        name=tag.name,
                        original_name=tag.name,
                        modified=False,
                    ))
                sess.flush()

                # Normalize tags and deduplicate via raw SQL (bypass ORM unit-of-work
                # to avoid UNIQUE constraint violations from batched UPDATE ordering).
                from .tags import is_junk_tag, normalize_tag

                sim_tags_all = sess.query(SimTag).all()

                # Compute normalized name and survivor (lowest tag_id) per norm key
                _tag_normed: dict[int, str] = {}
                _norm_to_survivor: dict[str, int] = {}
                for stag in sim_tags_all:
                    normed = normalize_tag(stag.name)
                    _tag_normed[stag.tag_id] = normed
                    key = normed.lower()
                    if key not in _norm_to_survivor or stag.tag_id < _norm_to_survivor[key]:
                        _norm_to_survivor[key] = stag.tag_id

                survivor_ids = set(_norm_to_survivor.values())
                duplicate_ids = {stag.tag_id for stag in sim_tags_all} - survivor_ids

                # Raw SQL: delete duplicates first, then rename survivors
                # This avoids ORM batching all UPDATEs before DELETEs are committed.
                if duplicate_ids:
                    placeholders = ",".join(str(i) for i in duplicate_ids)
                    sess.execute(text(f"DELETE FROM sim_tags WHERE tag_id IN ({placeholders})"))

                # Two-phase rename to avoid ordering conflicts (e.g. "consciousness" →
                # "consciousnes" conflicts with existing tag named "consciousnes" that
                # will itself be renamed to "consciousne").
                # Phase 1: rename to guaranteed-unique temp names.
                for tag_id in survivor_ids:
                    sess.execute(
                        text("UPDATE sim_tags SET name=:name WHERE tag_id=:tid"),
                        {"name": f"__tmp__{tag_id}__", "tid": tag_id},
                    )
                # Phase 2: rename from temp to final normalized name.
                for tag_id, normed in _tag_normed.items():
                    if tag_id in survivor_ids:
                        sess.execute(
                            text("UPDATE sim_tags SET name=:name WHERE tag_id=:tid"),
                            {"name": normed, "tid": tag_id},
                        )

                sess.expire_all()  # re-sync ORM identity map with DB state

                # Seed junk and MeSH tags into ExcludedTag
                _junk_mesh_excluded: dict[str, str] = {}
                for stag in sess.query(SimTag).all():
                    if is_junk_tag(stag.name):
                        _junk_mesh_excluded[stag.name.lower()] = "junk"
                    elif (
                        len(stag.name) > 0
                        and stag.name[0].isupper()
                        and (":" in stag.name or "," in stag.name)
                    ):
                        _junk_mesh_excluded[stag.name.lower()] = "mesh"
                for name, reason in _junk_mesh_excluded.items():
                    # Only insert if not already present from config seeding above
                    if not sess.query(ExcludedTag).filter_by(name=name).first():
                        sess.add(ExcludedTag(name=name, reason=reason))
                sess.flush()

                # Insert items
                for item in library.items.values():
                    sess.add(SimItem(
                        item_id=item.item_id,
                        key=item.key,
                        item_type=item.item_type,
                        title=item.title,
                        date=item.date,
                        doi=item.doi,
                        url=item.url,
                        date_added=item.date_added,
                        creators_json=json.dumps(item.creators),
                        metadata_json=json.dumps(item.metadata),
                    ))
                sess.flush()

                # Insert item-tag links — strip /unread at ingestion (meaningless)
                _unread_ids = {
                    t.tag_id for t in library.tags.values()
                    if t.name.lower().strip() in {"/unread", "unread"}
                }
                for item in library.items.values():
                    for tid in item.tags:
                        if tid in _unread_ids:
                            continue
                        sess.add(SimItemTag(item_id=item.item_id, tag_id=tid))

                # Insert collection-item links
                for col in library.collections.values():
                    for iid in col.items:
                        sess.add(SimCollectionItem(
                            collection_id=col.collection_id, item_id=iid
                        ))

                # Seed excluded tags from config (user can edit via UI)
                _excluded: dict[str, str] = {}
                for t in TAG_STATUS_VALUES:
                    _excluded[t.lower()] = "status"
                for t in TAG_QUALITY_VALUES:
                    _excluded[t.lower()] = "quality"
                for t in TAG_PROTECTED:
                    _excluded[t.lower()] = "protected"
                for name, reason in _excluded.items():
                    sess.add(ExcludedTag(name=name, reason=reason))
                sess.flush()

                # Session provenance row
                sess.add(SessionMeta(
                    zotero_sqlite_path=str(zotero_sqlite_path),
                    chroma_db_path=str(chroma_db_path),
                    chroma_collection=chroma_collection,
                    embedding_model=embedding_model,
                    worker_params=None,
                    import_timestamp=datetime.datetime.now(
                        datetime.timezone.utc
                    ).isoformat(),
                    worker_timestamp=None,
                    library_version=library_version,
                    item_count=len(library.items),
                    tag_count=len(library.tags),
                ))

                sess.commit()
        except SQLAlchemyError as exc:
            # Rollback is automatic on context-manager exit
            raise IOError(
                f"Failed to clone library into simulation database: {exc}"
            ) from exc

    # ── CRUD operations ───────────────────────────────────────────────────────

    def _log(self, sess: Session, **kwargs) -> None:
        sess.add(ChangeLog(
            timestamp=datetime.datetime.now(datetime.timezone.utc).isoformat(),
            **kwargs
        ))

    def rename_collection(self, collection_id: int, new_name: str) -> None:
        """Rename a collection.  Records old and new name in changelog."""
        with self.session() as sess:
            col = sess.get(SimCollection, collection_id)
            if col is None:
                raise KeyError(f"Collection {collection_id} not found in simulation DB.")
            old_name = col.name
            col.name = new_name
            col.modified = True
            self._log(
                sess,
                entity_type="collection",
                entity_id=collection_id,
                operation="rename",
                field_name="name",
                old_value=old_name,
                new_value=new_name,
                description=f"Renamed collection '{old_name}' -> '{new_name}'",
            )
            sess.commit()

    def move_items(
        self,
        item_ids: list[int],
        from_collection_id: int,
        to_collection_id: int,
    ) -> None:
        """Reassign items from one collection to another."""
        with self.session() as sess:
            for iid in item_ids:
                link = (
                    sess.query(SimCollectionItem)
                    .filter_by(collection_id=from_collection_id, item_id=iid)
                    .first()
                )
                if link is None:
                    continue
                sess.delete(link)
                sess.flush()
                # Check if already in target collection
                exists = (
                    sess.query(SimCollectionItem)
                    .filter_by(collection_id=to_collection_id, item_id=iid)
                    .first()
                )
                if not exists:
                    sess.add(SimCollectionItem(
                        collection_id=to_collection_id, item_id=iid
                    ))
                self._log(
                    sess,
                    entity_type="item",
                    entity_id=iid,
                    operation="move",
                    field_name="collection",
                    old_value=str(from_collection_id),
                    new_value=str(to_collection_id),
                    description=f"Moved item {iid} from col {from_collection_id} to {to_collection_id}",
                )
            sess.commit()

    def merge_tags(self, tag_ids: list[int], target_name: str) -> SimTag:
        """
        Merge multiple tags into one tag with target_name.

        All item-tag links from the source tags are repointed to the surviving
        tag.  Source tags (not equal to the target) are deleted.
        """
        with self.session() as sess:
            # Find or create the target tag
            target = sess.query(SimTag).filter_by(name=target_name).first()
            if target is None:
                # Pick the first matched id or create new
                target = sess.get(SimTag, tag_ids[0])
                if target is None:
                    raise KeyError(f"Tag {tag_ids[0]} not found.")
                old_target_name = target.name
                target.name = target_name
                target.modified = True
                self._log(
                    sess,
                    entity_type="tag",
                    entity_id=target.tag_id,
                    operation="rename",
                    field_name="name",
                    old_value=old_target_name,
                    new_value=target_name,
                    description=f"Renamed tag '{old_target_name}' -> '{target_name}' (merge target)",
                )
                sess.flush()

            target_id = target.tag_id

            for tid in tag_ids:
                if tid == target_id:
                    continue
                source = sess.get(SimTag, tid)
                if source is None:
                    continue

                # Repoint all item-tag links
                links = sess.query(SimItemTag).filter_by(tag_id=tid).all()
                for link in links:
                    iid = link.item_id
                    # Avoid duplicate
                    exists = (
                        sess.query(SimItemTag)
                        .filter_by(item_id=iid, tag_id=target_id)
                        .first()
                    )
                    if not exists:
                        sess.add(SimItemTag(item_id=iid, tag_id=target_id))
                    sess.delete(link)

                self._log(
                    sess,
                    entity_type="tag",
                    entity_id=tid,
                    operation="merge",
                    old_value=source.name,
                    new_value=target_name,
                    description=f"Merged tag '{source.name}' (id={tid}) into '{target_name}'",
                )
                sess.flush()
                sess.delete(source)

            sess.commit()
            return target

    def add_tag_category(self, tag_id: int, category: str) -> None:
        """Assign a category label to a tag."""
        with self.session() as sess:
            tag = sess.get(SimTag, tag_id)
            if tag is None:
                raise KeyError(f"Tag {tag_id} not found.")
            old = tag.category
            tag.category = category
            tag.modified = True
            self._log(
                sess,
                entity_type="tag",
                entity_id=tag_id,
                operation="categorize",
                field_name="category",
                old_value=old,
                new_value=category,
                description=f"Set category '{category}' on tag '{tag.name}'",
            )
            sess.commit()

    def normalize_tags(self, rules: dict[str, str]) -> int:
        """
        Apply normalization rules: {old_name -> new_name}.

        Returns the number of tags renamed.
        """
        count = 0
        with self.session() as sess:
            for old_name, new_name in rules.items():
                tag = sess.query(SimTag).filter_by(name=old_name).first()
                if tag is None:
                    continue
                tag.name = new_name
                tag.modified = True
                self._log(
                    sess,
                    entity_type="tag",
                    entity_id=tag.tag_id,
                    operation="normalize",
                    field_name="name",
                    old_value=old_name,
                    new_value=new_name,
                    description=f"Normalized tag '{old_name}' -> '{new_name}'",
                )
                count += 1
            sess.commit()
        return count

    # ── Session metadata ───────────────────────────────────────────────────────

    def get_meta(self) -> SessionMeta | None:
        """Return the single SessionMeta row, or None if not yet written."""
        with self.session() as sess:
            return sess.query(SessionMeta).order_by(SessionMeta.id).first()

    def record_worker_run(self, params: dict) -> None:
        """Stamp the SessionMeta row with worker params and a timestamp."""
        with self.session() as sess:
            meta = sess.query(SessionMeta).order_by(SessionMeta.id).first()
            if meta is None:
                return
            meta.worker_params = json.dumps(params)
            meta.worker_timestamp = datetime.datetime.now(
                datetime.timezone.utc
            ).isoformat()
            sess.commit()

    # ── Diff ─────────────────────────────────────────────────────────────────

    def diff(self) -> ChangeSet:
        """Return a ChangeSet of all mutations recorded in the changelog."""
        with self.session() as sess:
            rows = sess.query(ChangeLog).order_by(ChangeLog.id).all()
            changes = [
                Change(
                    entity_type=r.entity_type,
                    entity_id=r.entity_id,
                    operation=r.operation,
                    field_name=r.field_name,
                    old_value=r.old_value,
                    new_value=r.new_value,
                    description=r.description,
                )
                for r in rows
            ]
        return ChangeSet(changes=changes)

    # ── Validate ─────────────────────────────────────────────────────────────

    def validate(self) -> "ValidationReport":
        """Run validation checks on the current simulation state."""
        from .library import Collection, Item, Library, Tag
        from .validation import validate

        with self.session() as sess:
            cols_db = sess.query(SimCollection).all()
            items_db = sess.query(SimItem).all()
            tags_db = sess.query(SimTag).all()
            item_tags_db = sess.query(SimItemTag).all()
            col_items_db = sess.query(SimCollectionItem).all()

        # Reconstruct a Library-like object from the simulation state
        collections: dict[int, Collection] = {}
        for c in cols_db:
            collections[c.collection_id] = Collection(
                collection_id=c.collection_id,
                key=c.key,
                name=c.name,
                parent_id=c.parent_id,
            )

        items: dict[int, Item] = {}
        for i in items_db:
            items[i.item_id] = Item(
                item_id=i.item_id,
                key=i.key,
                item_type=i.item_type,
                title=i.title,
                creators=json.loads(i.creators_json or "[]"),
                date=i.date,
                doi=i.doi,
                url=i.url,
                metadata=json.loads(i.metadata_json or "{}"),
            )

        tags: dict[int, Tag] = {}
        for t in tags_db:
            tags[t.tag_id] = Tag(tag_id=t.tag_id, name=t.name)

        for it in item_tags_db:
            if it.item_id in items and it.tag_id in tags:
                items[it.item_id].tags.add(it.tag_id)
                tags[it.tag_id].items.add(it.item_id)

        for ci in col_items_db:
            if ci.item_id in items and ci.collection_id in collections:
                items[ci.item_id].collections.add(ci.collection_id)
                collections[ci.collection_id].items.add(ci.item_id)

        for cid, col in collections.items():
            if col.parent_id is not None and col.parent_id in collections:
                collections[col.parent_id].children.add(cid)

        lib = Library(items=items, collections=collections, tags=tags)
        return validate(lib)

    # ── Rollback ─────────────────────────────────────────────────────────────

    def close(self) -> None:
        """Dispose the SQLAlchemy engine, releasing all pooled connections."""
        self._engine.dispose()

    def __del__(self) -> None:
        """Ensure engine is disposed on garbage collection."""
        try:
            self._engine.dispose()
        except Exception:
            pass

    def __enter__(self) -> "SimulationDB":
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()

    def rollback(self) -> None:
        """Delete the simulation database file to start fresh."""
        self.close()
        if self._path.exists():
            self._path.unlink()


# ── Convenience function ──────────────────────────────────────────────────────

def clone(
    library: "Library",
    db_path: Path = SIMULATION_DB_PATH,
    **provenance,
) -> SimulationDB:
    """Create a SimulationDB and clone the given Library into it.

    Extra keyword arguments (``zotero_sqlite_path``, ``chroma_db_path``,
    ``chroma_collection``, ``embedding_model``, ``library_version``) are
    forwarded to :meth:`SimulationDB.clone`.
    """
    sim = SimulationDB(db_path=db_path)
    sim.clone(library, **provenance)
    return sim
