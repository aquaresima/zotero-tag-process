"""Tests for the Ollama batch tagger (no live Ollama; LLM is injected)."""

from __future__ import annotations

from tag_generation import worker as W
from tag_generation.config import ensure_shared_on_path

ensure_shared_on_path()

from zotero_restructuring.models import TagProposal  # noqa: E402
from zotero_restructuring.simulation import SimulationDB  # noqa: E402


def _fake_llm(scores=None):
    """Return an llm_fn that tags every paper with a fixed set."""
    def _fn(papers, vocab, *, backend, model, ollama_base_url):
        out = {}
        for iid, title, abstract, existing in papers:
            out[iid] = {
                "tags": [("auditory cortex", 0.9), ("spiking networks", 0.8),
                         ("streaming", 0.6)],
                "methods": [("simulation", 0.95)],
                "fields": [("area/auditory-cortex", 0.7)],
            }
        return out
    return _fn


def test_parse_batch_response_results_wrapper():
    text = ('{"results":[{"item_id":1,'
            '"freeform_tags":[{"name":"x","score":0.9}],'
            '"vocab_tags":["y"],"methods":["simulation"],"fields":["a/b"]}]}')
    parsed = W.parse_batch_response(text)
    assert 1 in parsed
    names = [n for n, _ in parsed[1]["tags"]]
    assert "x" in names and "y" in names
    assert parsed[1]["methods"][0][0] == "simulation"


def test_parse_batch_response_bare_array():
    text = '[{"item_id":2,"tags":[{"name":"z","score":0.5}]}]'
    parsed = W.parse_batch_response(text)
    assert parsed[2]["tags"][0][0] == "z"


def test_parse_batch_response_garbage():
    assert W.parse_batch_response("not json at all") == {}


def test_run_worker_processes_all_papers(gen_db):
    result = W.run_worker(gen_db, llm_fn=_fake_llm())
    assert result.items_processed == 3
    assert result.proposals_written > 0
    sim = SimulationDB(db_path=gen_db)
    with sim.session() as sess:
        # Every item got at least one general proposal.
        item_ids = {p.item_id for p in sess.query(TagProposal).all()}
        assert item_ids == {1, 2, 3}
        cats = {p.category for p in sess.query(TagProposal).all()}
        assert {"general", "method", "field"} <= cats
    sim.close()


def test_run_worker_normalizes_tags(gen_db):
    W.run_worker(gen_db, llm_fn=_fake_llm())
    sim = SimulationDB(db_path=gen_db)
    with sim.session() as sess:
        names = {p.tag_name for p in sess.query(TagProposal)
                 .filter_by(category="general").all()}
        # "spiking networks" -> singularized "spiking network"
        assert "spiking network" in names
    sim.close()


def test_idempotency_skips_decided_items(gen_db):
    W.run_worker(gen_db, llm_fn=_fake_llm())
    sim = SimulationDB(db_path=gen_db)
    with sim.session() as sess:
        p = sess.query(TagProposal).filter_by(item_id=1).first()
        p.status = "approved"
        sess.commit()
    sim.close()

    result = W.run_worker(gen_db, llm_fn=_fake_llm())
    # Item 1 is decided -> skipped; items 2 and 3 reprocessed.
    assert result.skipped_decided == 1
    assert result.items_processed == 2

    sim = SimulationDB(db_path=gen_db)
    with sim.session() as sess:
        approved = sess.query(TagProposal).filter_by(item_id=1, status="approved").count()
        assert approved == 1  # human decision preserved
    sim.close()


def test_idempotency_overwrites_pending(gen_db):
    W.run_worker(gen_db, llm_fn=_fake_llm())
    sim = SimulationDB(db_path=gen_db)
    with sim.session() as sess:
        before = sess.query(TagProposal).filter_by(item_id=2).count()
    sim.close()
    W.run_worker(gen_db, llm_fn=_fake_llm())
    sim = SimulationDB(db_path=gen_db)
    with sim.session() as sess:
        after = sess.query(TagProposal).filter_by(item_id=2).count()
    sim.close()
    assert before == after  # pending overwritten, not duplicated
