"""
library.py — In-memory representation of the full Zotero library.

Constructs Item, Collection, and Tag dataclasses from raw reader output
and provides query/filter methods.  Pure Python, no I/O.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable

from .reader import RawLibraryData


# ── Domain dataclasses ────────────────────────────────────────────────────────

@dataclass
class Item:
    item_id: int
    key: str
    item_type: str
    title: str | None
    creators: list[str]
    date: str | None
    doi: str | None
    url: str | None
    tags: set[int] = field(default_factory=set)
    collections: set[int] = field(default_factory=set)
    metadata: dict[str, str] = field(default_factory=dict)
    date_added: str | None = None

    def __hash__(self) -> int:
        return hash(self.item_id)

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Item):
            return NotImplemented
        return self.item_id == other.item_id


@dataclass
class Collection:
    collection_id: int
    key: str
    name: str
    parent_id: int | None
    children: set[int] = field(default_factory=set)
    items: set[int] = field(default_factory=set)

    def __hash__(self) -> int:
        return hash(self.collection_id)

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Collection):
            return NotImplemented
        return self.collection_id == other.collection_id


@dataclass
class Tag:
    tag_id: int
    name: str
    items: set[int] = field(default_factory=set)

    def __hash__(self) -> int:
        return hash(self.tag_id)

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Tag):
            return NotImplemented
        return self.tag_id == other.tag_id


# ── Library ───────────────────────────────────────────────────────────────────

class Library:
    """In-memory representation of a Zotero library.

    Attributes
    ----------
    items : dict[int, Item]
        itemID -> Item
    collections : dict[int, Collection]
        collectionID -> Collection
    tags : dict[int, Tag]
        tagID -> Tag
    """

    def __init__(
        self,
        items: dict[int, Item],
        collections: dict[int, Collection],
        tags: dict[int, Tag],
    ) -> None:
        self.items = items
        self.collections = collections
        self.tags = tags

    # ── Construction ──────────────────────────────────────────────────────────

    @classmethod
    def from_raw(cls, raw: RawLibraryData) -> "Library":
        """Build a Library from RawLibraryData produced by reader.read_library()."""
        # Build Items
        items: dict[int, Item] = {}
        for iid, d in raw.items.items():
            items[iid] = Item(
                item_id=iid,
                key=d["key"],
                item_type=d["item_type"],
                title=d.get("title"),
                creators=list(d.get("creators", [])),
                date=d.get("date"),
                doi=d.get("doi"),
                url=d.get("url"),
                date_added=d.get("date_added"),
                metadata=dict(d.get("metadata", {})),
            )

        # Build Collections
        collections: dict[int, Collection] = {}
        for cid, d in raw.collections.items():
            collections[cid] = Collection(
                collection_id=cid,
                key=d["key"],
                name=d["name"],
                parent_id=d.get("parent_id"),
            )

        # Build Tags
        tags: dict[int, Tag] = {}
        for tid, d in raw.tags.items():
            tags[tid] = Tag(tag_id=tid, name=d["name"])

        # Wire item-tag relationships
        for rel in raw.item_tags:
            iid, tid = rel["item_id"], rel["tag_id"]
            if iid in items and tid in tags:
                items[iid].tags.add(tid)
                tags[tid].items.add(iid)

        # Wire item-collection relationships
        for rel in raw.collection_items:
            cid, iid = rel["collection_id"], rel["item_id"]
            if iid in items and cid in collections:
                items[iid].collections.add(cid)
                collections[cid].items.add(iid)

        # Wire collection parent-child relationships
        for cid, col in collections.items():
            if col.parent_id is not None and col.parent_id in collections:
                collections[col.parent_id].children.add(cid)

        # Filter out orphaned tags (tags with no items in the primary library)
        # These exist in the database but only belong to items in other libraries
        tags = {tid: tag for tid, tag in tags.items() if tag.items}

        return cls(items=items, collections=collections, tags=tags)

    # ── Query methods ─────────────────────────────────────────────────────────

    def items_in_collection(self, collection_id: int) -> list[Item]:
        """Return all items in a given collection."""
        col = self.collections.get(collection_id)
        if col is None:
            return []
        return [self.items[iid] for iid in col.items if iid in self.items]

    def items_with_tag(self, tag_id: int) -> list[Item]:
        """Return all items that have a given tag."""
        tag = self.tags.get(tag_id)
        if tag is None:
            return []
        return [self.items[iid] for iid in tag.items if iid in self.items]

    def items_with_tag_name(self, name: str) -> list[Item]:
        """Return items that have any tag whose name matches (case-insensitive)."""
        name_lower = name.lower()
        result: list[Item] = []
        for tag in self.tags.values():
            if tag.name.lower() == name_lower:
                result.extend(self.items_with_tag(tag.tag_id))
        return result

    def find_items(self, query: str) -> list[Item]:
        """Return items whose title contains query (case-insensitive)."""
        q = query.lower()
        return [
            item for item in self.items.values()
            if item.title and q in item.title.lower()
        ]

    def orphaned_items(self) -> list[Item]:
        """Return items that belong to no collection."""
        return [
            item for item in self.items.values()
            if not item.collections
        ]

    def collection_subtree(self, collection_id: int) -> set[int]:
        """Return the set of collection IDs in the subtree rooted at collection_id."""
        result: set[int] = set()
        stack = [collection_id]
        while stack:
            cid = stack.pop()
            if cid in result:
                continue
            result.add(cid)
            col = self.collections.get(cid)
            if col:
                stack.extend(col.children)
        return result

    def root_collections(self) -> list[Collection]:
        """Return top-level collections (no parent)."""
        return [c for c in self.collections.values() if c.parent_id is None]

    def tag_by_name(self, name: str) -> Tag | None:
        """Return the first Tag whose name matches (case-insensitive), or None."""
        name_lower = name.lower()
        for tag in self.tags.values():
            if tag.name.lower() == name_lower:
                return tag
        return None

    # ── Statistics ────────────────────────────────────────────────────────────

    def stats(self) -> dict[str, int]:
        return {
            "items": len(self.items),
            "collections": len(self.collections),
            "tags": len(self.tags),
            "orphaned_items": len(self.orphaned_items()),
        }

    def __repr__(self) -> str:
        s = self.stats()
        return (
            f"<Library items={s['items']} collections={s['collections']} "
            f"tags={s['tags']} orphans={s['orphaned_items']}>"
        )
