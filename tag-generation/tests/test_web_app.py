"""Tests for the review web API using a pre-populated generatedtags DB."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from tag_generation import worker as W
from tag_generation.config import ensure_shared_on_path
from tag_generation.web.app import create_app

ensure_shared_on_path()

from zotero_restructuring.models import TagProposal  # noqa: E402
from zotero_restructuring.simulation import SimulationDB  # noqa: E402


def _fake_llm(papers, vocab, *, backend, model, ollama_base_url):
    out = {}
    for i, (iid, title, abstract, existing) in enumerate(papers):
        out[iid] = {
            # Descending confidence so item ordering is deterministic.
            "tags": [("alpha tag", 0.9 - 0.1 * iid), ("beta tag", 0.5)],
            "methods": [], "fields": [],
        }
    return out


@pytest.fixture()
def client(gen_db):
    W.run_worker(gen_db, llm_fn=_fake_llm)
    app = create_app(gen_db)
    return TestClient(app), gen_db


def test_health(client):
    c, _ = client
    r = c.get("/api/health").json()
    assert r["status"] == "ok"
    assert r["proposal_count"] > 0


def test_proposals_sorted_by_avg_confidence_desc(client):
    c, _ = client
    data = c.get("/api/proposals").json()
    avgs = [it["avg_confidence"] for it in data["items"]]
    assert avgs == sorted(avgs, reverse=True)


def test_approve_then_paper_disappears_when_all_decided(client):
    c, db = client
    data = c.get("/api/proposals").json()
    item = data["items"][0]
    iid = item["item_id"]
    for p in item["proposals"]:
        c.post(f"/api/proposal/{p['id']}/approve")
    data2 = c.get("/api/proposals").json()
    remaining = {it["item_id"] for it in data2["items"]}
    assert iid not in remaining


def test_reject_keeps_other_pending(client):
    c, _ = client
    data = c.get("/api/proposals").json()
    p = data["items"][0]["proposals"][0]
    r = c.post(f"/api/proposal/{p['id']}/reject").json()
    assert r["status"] == "rejected"


def test_item_approve_all_above_threshold(client):
    c, _ = client
    data = c.get("/api/proposals").json()
    iid = data["items"][0]["item_id"]
    r = c.post(f"/api/item/{iid}/approve-all?threshold=0.7").json()
    assert r["approved"] >= 1


def test_unapprove_all(client):
    c, _ = client
    data = c.get("/api/proposals").json()
    iid = data["items"][0]["item_id"]
    c.post(f"/api/item/{iid}/approve-all?threshold=0.0")
    r = c.post(f"/api/item/{iid}/unapprove-all").json()
    assert r["reset"] >= 1


def test_bulk_approve(client):
    c, _ = client
    r = c.post("/api/proposals/bulk-approve?threshold=0.7").json()
    assert r["approved"] >= 1


def test_pagination_shape(client):
    c, _ = client
    data = c.get("/api/proposals?page=1").json()
    assert data["page"] == 1
    assert data["total_pages"] >= 1
    assert "counts" in data
