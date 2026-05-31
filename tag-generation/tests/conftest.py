"""Shared fixtures: build a small generatedtags DB without a live Zotero/Ollama."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tag_generation.config import ensure_shared_on_path
from tag_generation.ingest import allow_output_path

ensure_shared_on_path()

from zotero_restructuring.models import SimItem, SimTag, SimItemTag, SessionMeta  # noqa: E402
from zotero_restructuring.simulation import SimulationDB  # noqa: E402


@pytest.fixture()
def gen_db(tmp_path: Path) -> Path:
    """A minimal generatedtags DB with 3 items and a meta row."""
    db_path = tmp_path / "zotero_generatedtags_test.sqlite"
    allow_output_path(db_path)
    sim = SimulationDB(db_path=db_path)
    with sim.session() as sess:
        sess.add(SessionMeta(
            zotero_sqlite_path="/tmp/zotero.sqlite",
            item_count=3, tag_count=2,
        ))
        for i, (title, abstract) in enumerate([
            ("Spiking networks in auditory cortex", "We model auditory streaming."),
            ("Dendritic computation review", "A review of dendrites."),
            ("Reinforcement learning agents", "RL in continuous control."),
        ], start=1):
            sess.add(SimItem(
                item_id=i, key=f"KEY{i:04d}", item_type="journalArticle",
                title=title,
                creators_json=json.dumps([f"Author {i}"]),
                metadata_json=json.dumps({"abstractNote": abstract}),
            ))
        sess.add(SimTag(tag_id=1, name="neuroscience"))
        sess.add(SimTag(tag_id=2, name="modeling"))
        sess.add(SimItemTag(item_id=1, tag_id=1))
        sess.commit()
    sim.close()
    return db_path
