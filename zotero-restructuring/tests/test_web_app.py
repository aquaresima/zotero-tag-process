"""
test_web_app.py — Tests for the simplified web/app.py via FastAPI's TestClient.

A small simulation DB is built from the shared ``sample_library`` fixture and a
couple of approved TagProposal rows are inserted so the post-merge views and the
status ``any_approved`` flag have data.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from zotero_restructuring.models import SimItem, SimTag, TagProposal
from zotero_restructuring.simulation import clone
from zotero_restructuring.web.app import create_app


@pytest.fixture
def sim_with_approved(sample_library, sim_db_path):
    """Cloned sim DB with one approved proposal on a real item."""
    sim = clone(sample_library, db_path=sim_db_path)
    with sim.session() as sess:
        item_ids = [i.item_id for i in sess.query(SimItem).order_by(SimItem.item_id).all()]
        tag_names = [t.name for t in sess.query(SimTag).all()]
        sess.add(TagProposal(item_id=item_ids[0], tag_name="proposed-a",
                             confidence=1.0, status="approved",
                             generated_by="imported", category="general"))
        sess.commit()
    sim.close()
    return sim_db_path, tag_names, item_ids


@pytest.fixture
def client(sim_with_approved):
    db_path, _tags, _ids = sim_with_approved
    return TestClient(create_app(db_path=db_path))


class TestPages:
    def test_root_ok(self, client):
        assert client.get("/").status_code == 200

    def test_stats_page_renders(self, client):
        assert client.get("/stats").status_code == 200

    def test_removed_pages_404(self, client):
        for path in ("/network", "/proposals", "/log", "/save", "/inbox"):
            assert client.get(path).status_code == 404


class TestStatusAndTags:
    def test_status_shape(self, client):
        s = client.get("/api/status").json()
        for key in ("imported", "any_approved", "item_count", "tag_count",
                    "apply_running", "import_running"):
            assert key in s
        assert s["item_count"] == 10
        assert s["tag_count"] == 15
        assert s["any_approved"] is True

    def test_status_no_worker_fields(self, client):
        s = client.get("/api/status").json()
        assert "worker_done" not in s
        assert "worker_running" not in s

    def test_tags_returns_names(self, sim_with_approved, client):
        _db, tags, _ids = sim_with_approved
        got = client.get("/api/tags").json()
        assert set(got) == set(tags)


class TestGraphAndStats:
    def test_paper_graph(self, client):
        d = client.get("/api/graph?space=papers").json()
        assert d["space"] == "papers"
        assert "nodes" in d and "edges" in d

    def test_tag_graph(self, client):
        d = client.get("/api/graph?space=tags").json()
        assert d["space"] == "tags"

    def test_stats_original(self, client):
        d = client.get("/api/stats?db=original").json()
        assert "summary" in d and "zipf" in d
        assert d["summary"]["total_items"] == 10

    def test_stats_post_merge(self, client):
        d = client.get("/api/stats?db=post").json()
        assert "summary" in d


class TestExcludedTags:
    def test_add_and_remove(self, client):
        r = client.post("/api/excluded-tags", json={"name": "TestExcl"})
        assert r.status_code == 200
        tid = r.json()["id"]
        names = {row["name"] for row in client.get("/api/excluded-tags").json()}
        assert "testexcl" in names
        assert client.delete(f"/api/excluded-tags/{tid}").status_code == 200


class TestHealthAndSummary:
    def test_health(self, client):
        r = client.get("/api/health").json()
        assert r["status"] == "ok"
        assert r["proposal_count"] >= 1

    def test_import_summary_empty_before_import(self, client):
        # No import run in this fixture -> empty summary dict.
        assert client.get("/api/import-summary").json() == {}
