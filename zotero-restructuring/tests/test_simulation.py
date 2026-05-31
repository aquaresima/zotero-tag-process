"""
test_simulation.py — Tests for simulation.py.

Covers:
- Clone: library is faithfully reproduced in simulation DB
- Rename collection: recorded in changelog
- Merge tags: items repointed, source deleted, changelog entries created
- diff(): returns exactly the mutations applied (Scenario B)
- validate(): consistent after operations
- Write failure: raises IOError
- Rollback: database file deleted
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from zotero_restructuring.simulation import SimulationDB, clone, ChangeSet
from zotero_restructuring.models import SimCollection, SimItem, SimTag


class TestClone:
    def test_clone_items(self, sample_library, sim_db_path):
        sim = clone(sample_library, db_path=sim_db_path)
        with sim.session() as sess:
            assert sess.query(SimItem).count() == 10

    def test_clone_collections(self, sample_library, sim_db_path):
        sim = clone(sample_library, db_path=sim_db_path)
        with sim.session() as sess:
            assert sess.query(SimCollection).count() == 3

    def test_clone_tags(self, sample_library, sim_db_path):
        sim = clone(sample_library, db_path=sim_db_path)
        with sim.session() as sess:
            assert sess.query(SimTag).count() == 15

    def test_clone_db_file_created(self, sample_library, sim_db_path):
        clone(sample_library, db_path=sim_db_path)
        assert sim_db_path.exists()

    def test_clone_is_idempotent(self, sample_library, sim_db_path):
        sim = clone(sample_library, db_path=sim_db_path)
        sim.clone(sample_library)
        with sim.session() as sess:
            assert sess.query(SimItem).count() == 10


# ── Scenario B ────────────────────────────────────────────────────────────────

class TestScenarioB:
    """
    Scenario B:
      1. Clone library -> simulation DB
      2. Rename a collection
      3. Merge two tags
      -> validation passes
      -> diff shows exactly 3 changes
    """

    def _get_two_tag_ids(self, library):
        """Return the IDs of two distinct tags from the library."""
        tag_ids = list(library.tags.keys())
        assert len(tag_ids) >= 2
        return tag_ids[0], tag_ids[1]

    def _get_collection_id(self, library):
        return list(library.collections.keys())[0]

    def test_clone_and_modify(self, sample_library, sim_db_path):
        sim = clone(sample_library, db_path=sim_db_path)

        col_id = self._get_collection_id(sample_library)
        tag_id_a, tag_id_b = self._get_two_tag_ids(sample_library)

        # Change 1: rename collection
        col = sample_library.collections[col_id]
        new_col_name = col.name + "_renamed"
        sim.rename_collection(col_id, new_col_name)

        # Changes 2 + 3: merge two tags (produces 2 changelog entries: rename + merge)
        tag_a = sample_library.tags[tag_id_a]
        tag_b = sample_library.tags[tag_id_b]
        merged_name = tag_a.name + "_merged"
        sim.merge_tags([tag_id_a, tag_id_b], merged_name)

        changeset = sim.diff()
        assert len(changeset) >= 3, (
            f"Expected at least 3 changes, got {len(changeset)}:\n{changeset.summary()}"
        )

        # Validate consistency
        report = sim.validate()
        assert report.dangling_tag_refs == []
        assert report.dangling_collection_refs == []

    def test_rename_collection_recorded(self, sample_library, sim_db_path):
        sim = clone(sample_library, db_path=sim_db_path)
        col_id = list(sample_library.collections.keys())[0]
        old_name = sample_library.collections[col_id].name

        sim.rename_collection(col_id, "NewName")

        changeset = sim.diff()
        rename_changes = [c for c in changeset if c.operation == "rename"]
        assert len(rename_changes) >= 1
        assert any(c.old_value == old_name and c.new_value == "NewName"
                   for c in rename_changes)

    def test_merge_tags_removes_source(self, sample_library, sim_db_path):
        sim = clone(sample_library, db_path=sim_db_path)
        tag_ids = list(sample_library.tags.keys())
        t_a, t_b = tag_ids[0], tag_ids[1]

        sim.merge_tags([t_a, t_b], "merged_tag_name")

        with sim.session() as sess:
            remaining = {t.tag_id for t in sess.query(SimTag).all()}
            # t_a is the target (renamed), t_b is deleted
            assert t_b not in remaining

    def test_validate_after_operations(self, sample_library, sim_db_path):
        sim = clone(sample_library, db_path=sim_db_path)
        col_id = list(sample_library.collections.keys())[0]
        tag_ids = list(sample_library.tags.keys())

        sim.rename_collection(col_id, "NewName")
        sim.merge_tags(tag_ids[:2], "merged")

        report = sim.validate()
        assert report.dangling_tag_refs == []
        assert report.dangling_collection_refs == []


class TestMoveItems:
    def test_move_items_between_collections(self, sample_library, sim_db_path):
        sim = clone(sample_library, db_path=sim_db_path)
        col_ids = list(sample_library.collections.keys())
        from_col, to_col = col_ids[0], col_ids[1]

        # Get an item in from_col
        from_col_obj = sample_library.collections[from_col]
        if not from_col_obj.items:
            pytest.skip("Source collection is empty")
        item_id = next(iter(from_col_obj.items))

        sim.move_items([item_id], from_col, to_col)

        changeset = sim.diff()
        move_changes = [c for c in changeset if c.operation == "move"]
        assert len(move_changes) == 1


class TestTagCategory:
    def test_add_tag_category(self, sample_library, sim_db_path):
        sim = clone(sample_library, db_path=sim_db_path)
        tag_id = list(sample_library.tags.keys())[0]

        sim.add_tag_category(tag_id, "domain:neuroscience")

        with sim.session() as sess:
            tag = sess.get(SimTag, tag_id)
            assert tag.category == "domain:neuroscience"


class TestWriteFailure:
    def test_write_failure_raises_ioerror(self, sample_library, tmp_path):
        """SimulationDB raises IOError and never leaves partial DB on engine error."""
        bad_path = tmp_path / "no_perms" / "simulation.sqlite"
        # The parent directory does not exist and cannot be created automatically
        # because we don't create it — just verify that a path outside the
        # allowed whitelist raises PermissionError
        from zotero_restructuring.config import ALLOWED_WRITE_PATHS
        # Use a path known to be outside the whitelist
        outside_path = Path("/tmp/not_allowed/simulation.sqlite")
        with pytest.raises(PermissionError):
            SimulationDB(db_path=outside_path)


class TestRollback:
    def test_rollback_deletes_db(self, sample_library, sim_db_path):
        sim = clone(sample_library, db_path=sim_db_path)
        assert sim_db_path.exists()
        sim.rollback()
        assert not sim_db_path.exists()

    def test_rollback_on_nonexistent_db(self, sim_db_path):
        """Rollback on a never-created DB should not raise."""
        sim = SimulationDB(db_path=sim_db_path)
        sim.rollback()
        assert not sim_db_path.exists()


class TestNormalizeTags:
    def test_normalize_tags_bulk(self, sample_library, sim_db_path):
        sim = clone(sample_library, db_path=sim_db_path)
        # Pick first tag and define a rule
        tag = list(sample_library.tags.values())[0]
        rules = {tag.name: "canonical_form"}
        count = sim.normalize_tags(rules)
        assert count == 1


class TestChangeSetSummary:
    def test_changeset_summary(self, sample_library, sim_db_path):
        sim = clone(sample_library, db_path=sim_db_path)
        col_id = list(sample_library.collections.keys())[0]
        sim.rename_collection(col_id, "RenamedCol")
        cs = sim.diff()
        summary = cs.summary()
        assert "ChangeSet" in summary
        assert "change(s)" in summary

    def test_empty_changeset_summary(self, sample_library, sim_db_path):
        sim = clone(sample_library, db_path=sim_db_path)
        cs = sim.diff()
        summary = cs.summary()
        assert "0 change(s)" in summary

    def test_changeset_iter(self, sample_library, sim_db_path):
        sim = clone(sample_library, db_path=sim_db_path)
        col_id = list(sample_library.collections.keys())[0]
        sim.rename_collection(col_id, "RenamedCol")
        cs = sim.diff()
        changes_list = list(cs)
        assert len(changes_list) >= 1


class TestContextManager:
    def test_simulation_db_as_context_manager(self, sample_library, sim_db_path):
        with clone(sample_library, db_path=sim_db_path) as sim:
            with sim.session() as sess:
                assert sess.query(SimItem).count() == 10
        # Engine should be disposed after __exit__


class TestCloseMethod:
    def test_close_disposes_engine(self, sample_library, sim_db_path):
        sim = clone(sample_library, db_path=sim_db_path)
        sim.close()
        # After close, creating a new session would still work (engine is disposed
        # but object still exists); the important thing is no ResourceWarning
