"""
import_tags.py — Import approved tags from a generatedtags DB into the sim DB.

The tag-generation tool produces ``zotero_generatedtags_DATE.sqlite`` (same ORM
schema as ``zotero_restructure.db``).  This module:

1. Clones the current ``zotero.sqlite`` into the sim DB (ingest + normalize).
2. Opens the generatedtags DB read-only.
3. Matches papers across the two DBs by Zotero key (``SimItem.key``).
4. For each approved proposal in the generatedtags DB, normalizes the tag name
   again (handles drift) and writes a ``TagProposal(status="approved")`` into
   the sim DB.
5. Skips a proposal when the normalized tag already exists in
   ``sim_item_tags`` for that item (idempotent merge).

Only approved proposals are imported; pending/rejected rows are ignored.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from .library import Library
from .models import SimItem, SimItemTag, SimTag, TagProposal
from .reader import read_library
from .simulation import clone
from .tags import normalize_tag


@dataclass
class ImportResult:
    """Summary of an import-tags run."""

    items_matched: int = 0
    proposals_imported: int = 0
    skipped_existing: int = 0
    skipped_unmatched: int = 0
    errors: list[str] = field(default_factory=list)


def _read_approved_by_key(generated_db: Path) -> dict[str, list[str]]:
    """Return {zotero_key: [approved tag names]} from the generatedtags DB."""
    engine = create_engine(f"sqlite:///{generated_db}")
    Session = sessionmaker(bind=engine)
    out: dict[str, list[str]] = {}
    try:
        with Session() as sess:
            key_by_item = {i.item_id: i.key for i in sess.query(SimItem).all()}
            for p in (
                sess.query(TagProposal)
                .filter(TagProposal.status.in_(("approved", "edited")))
                .all()
            ):
                key = key_by_item.get(p.item_id)
                if key is None:
                    continue
                out.setdefault(key, []).append(p.effective_tag)
    finally:
        engine.dispose()
    return out


def import_tags(
    generated_db: Path,
    *,
    sim_db_path: Path,
    zotero_sqlite_path: Path | None = None,
) -> ImportResult:
    """Ingest zotero.sqlite into the sim DB and import approved generated tags.

    Parameters
    ----------
    generated_db:
        Path to ``zotero_generatedtags_*.sqlite``.
    sim_db_path:
        Path for the (re)built ``zotero_restructure.db``.
    zotero_sqlite_path:
        Source Zotero DB; defaults to config's ``get_zotero_sqlite_path()``.
    """
    result = ImportResult()
    if not Path(generated_db).exists():
        raise FileNotFoundError(f"generatedtags DB not found: {generated_db}")

    # 1. Clone + normalize the live library into the sim DB.
    raw = read_library(zotero_sqlite_path)
    lib = Library.from_raw(raw)
    sim = clone(lib, db_path=sim_db_path, zotero_sqlite_path=zotero_sqlite_path)

    # 2. Read approved proposals from the generatedtags DB, keyed by Zotero key.
    approved_by_key = _read_approved_by_key(Path(generated_db))

    # 3-5. Match by key, normalize, dedup against existing item tags, write.
    with sim.session() as sess:
        item_by_key = {i.key: i for i in sess.query(SimItem).all()}
        tag_name_by_id = {t.tag_id: t.name for t in sess.query(SimTag).all()}

        # Existing (item_id -> set of current normalized tag names).
        existing: dict[int, set[str]] = {}
        for link in sess.query(SimItemTag).all():
            name = tag_name_by_id.get(link.tag_id)
            if name:
                existing.setdefault(link.item_id, set()).add(name.lower())

        # Already-written proposal pairs (avoid UNIQUE violations).
        written: set[tuple[int, str]] = {
            (p.item_id, p.tag_name) for p in sess.query(TagProposal).all()
        }

        for key, tags in approved_by_key.items():
            item = item_by_key.get(key)
            if item is None:
                result.skipped_unmatched += 1
                continue
            result.items_matched += 1
            for raw_tag in tags:
                norm = normalize_tag(raw_tag)
                if not norm:
                    continue
                if norm.lower() in existing.get(item.item_id, set()):
                    result.skipped_existing += 1
                    continue
                pair = (item.item_id, norm)
                if pair in written:
                    continue
                sess.add(TagProposal(
                    item_id=item.item_id,
                    tag_name=norm,
                    confidence=1.0,
                    status="approved",
                    is_new_tag=norm not in tag_name_by_id.values(),
                    generated_by="imported",
                    category="general",
                ))
                written.add(pair)
                result.proposals_imported += 1

        sess.commit()

    sim.close()
    return result
