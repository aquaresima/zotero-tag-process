"""
test_import_tags.py — Tests for importing approved tags from a generatedtags DB.

Builds a generatedtags DB whose items share Zotero keys with the source library,
adds approved/pending/rejected proposals, and verifies the matching, normalization,
dedup-against-existing, and approved-only filtering.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from zotero_restructuring.config import DATA_DIR
from zotero_restructuring.import_tags import import_tags
from zotero_restructuring.library import Library
from zotero_restructuring.models import (
    SimItem,
    SimItemTag,
    SimTag,
    TagProposal,
)
from zotero_restructuring.reader import read_library
from zotero_restructuring.simulation import SimulationDB, clone


@pytest.fixture
def generated_db(small_zotero_sqlite, tmp_path) -> Path:
    """A generatedtags DB sharing keys with the source library."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    gen_path = DATA_DIR / f"gen_{tmp_path.name}.sqlite"
    lib = Library.from_raw(read_library(small_zotero_sqlite))
    sim = clone(lib, db_path=gen_path, zotero_sqlite_path=small_zotero_sqlite)
    with sim.session() as sess:
        items = sess.query(SimItem).order_by(SimItem.item_id).all()
        # Approved (will import), pending + rejected (will be ignored).
        sess.add(TagProposal(item_id=items[0].item_id, tag_name="Neural-Networks",
                             confidence=0.9, status="approved", category="general"))
        sess.add(TagProposal(item_id=items[1].item_id, tag_name="dynamical systems",
                             confidence=0.8, status="approved", category="general"))
        sess.add(TagProposal(item_id=items[2].item_id, tag_name="ignored-pending",
                             confidence=0.5, status="pending", category="general"))
        sess.add(TagProposal(item_id=items[3].item_id, tag_name="ignored-rejected",
                             confidence=0.5, status="rejected", category="general"))
        sess.commit()
    sim.close()
    yield gen_path
    if gen_path.exists():
        gen_path.unlink()


@pytest.fixture
def sim_out(tmp_path) -> Path:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    p = DATA_DIR / f"out_{tmp_path.name}.sqlite"
    yield p
    if p.exists():
        p.unlink()


def test_imports_only_approved(small_zotero_sqlite, generated_db, sim_out):
    result = import_tags(
        generated_db, sim_db_path=sim_out, zotero_sqlite_path=small_zotero_sqlite,
    )
    assert result.proposals_imported == 2  # only the two approved
    sim = SimulationDB(db_path=sim_out)
    with sim.session() as sess:
        names = {p.tag_name for p in sess.query(TagProposal).all()}
        statuses = {p.status for p in sess.query(TagProposal).all()}
    sim.close()
    assert statuses == {"approved"}
    # "Neural-Networks" normalized -> "neural network"
    assert "neural network" in names
    assert "dynamical system" in names  # singularized
    assert "ignored-pending" not in names
    assert "ignored-rejected" not in names


def test_skips_existing_item_tags(small_zotero_sqlite, generated_db, sim_out):
    # Pre-seed: figure out an existing tag on item 0 and propose it as approved.
    lib = Library.from_raw(read_library(small_zotero_sqlite))
    pre = clone(lib, db_path=sim_out, zotero_sqlite_path=small_zotero_sqlite)
    with pre.session() as sess:
        item0 = sess.query(SimItem).order_by(SimItem.item_id).first()
        link = sess.query(SimItemTag).filter_by(item_id=item0.item_id).first()
        existing_name = sess.get(SimTag, link.tag_id).name
        item0_key = item0.key
    pre.close()

    # Build a fresh generated DB proposing that existing tag (approved).
    genlib = Library.from_raw(read_library(small_zotero_sqlite))
    gen2 = sim_out.with_name("gen2_" + sim_out.name)
    g = clone(genlib, db_path=gen2, zotero_sqlite_path=small_zotero_sqlite)
    with g.session() as sess:
        it = sess.query(SimItem).filter_by(key=item0_key).first()
        sess.add(TagProposal(item_id=it.item_id, tag_name=existing_name,
                             confidence=0.9, status="approved", category="general"))
        sess.commit()
    g.close()

    result = import_tags(gen2, sim_db_path=sim_out, zotero_sqlite_path=small_zotero_sqlite)
    assert result.skipped_existing >= 1
    assert result.proposals_imported == 0
    gen2.unlink()


def test_missing_generated_db_raises(small_zotero_sqlite, sim_out):
    with pytest.raises(FileNotFoundError):
        import_tags(Path("/nonexistent/gen.sqlite"), sim_db_path=sim_out,
                    zotero_sqlite_path=small_zotero_sqlite)
