"""Phase 2.5 — citation count enrichment via Semantic Scholar."""
from __future__ import annotations

import datetime
import logging
import time
from pathlib import Path
from queue import Queue

from .models import CitationInbox, SimItem, TagProposal
from .simulation import SimulationDB

logger = logging.getLogger("zotero_restructuring.citations")
_SS_BASE = "https://api.semanticscholar.org/graph/v1"


def _fetch_by_doi(doi: str, client) -> int | None:
    r = client.get(f"{_SS_BASE}/paper/{doi}", params={"fields": "citationCount"}, timeout=10)
    if r.status_code == 200:
        return r.json().get("citationCount")
    return None


def _fetch_by_title(title: str, client) -> int | None:
    r = client.get(
        f"{_SS_BASE}/paper/search",
        params={"query": title, "fields": "citationCount", "limit": 1},
        timeout=10,
    )
    if r.status_code == 200:
        data = r.json().get("data", [])
        if data:
            return data[0].get("citationCount")
    return None


def _percentile_tier(count: int, p25: float, p75: float) -> str:
    if count <= p25:
        return "niche"
    if count >= p75:
        return "high"
    return "medium"


def enrich_citations(db_path: Path, progress_queue: "Queue | None" = None) -> dict:
    try:
        import httpx
    except ImportError:
        return {"error": "httpx not installed"}

    sim = SimulationDB(db_path=db_path)
    result = {"found": 0, "not_found": 0, "errors": 0}

    with sim.session() as sess:
        items = sess.query(SimItem).filter(SimItem.citation_count == None).all()
        total = len(items)
        counts: dict[int, int] = {}

        with httpx.Client() as client:
            for i, item in enumerate(items):
                if progress_queue:
                    progress_queue.put({
                        "current": i + 1,
                        "total": total,
                        "title": item.title or "",
                        "phase": "running",
                    })
                try:
                    count = None
                    if item.doi:
                        count = _fetch_by_doi(item.doi.strip(), client)
                    if count is None and item.title:
                        count = _fetch_by_title(item.title, client)
                    if count is not None:
                        item.citation_count = count
                        counts[item.item_id] = count
                        result["found"] += 1
                        logger.info("citation found: item %d count=%d", item.item_id, count)
                    else:
                        sess.add(CitationInbox(
                            item_id=item.item_id,
                            title=item.title,
                            doi=item.doi,
                            reason="not found in Semantic Scholar",
                            created_at=datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S"),
                        ))
                        result["not_found"] += 1
                        logger.debug("citation not found: item %d '%s'", item.item_id, item.title)
                except Exception as e:
                    logger.warning("citation lookup failed for item %d: %s", item.item_id, e)
                    result["errors"] += 1
                time.sleep(1.0)

        sess.commit()

        # Compute impact tiers from percentiles
        if counts:
            vals = sorted(counts.values())
            p25 = vals[len(vals) // 4]
            p75 = vals[3 * len(vals) // 4]

            sess.query(TagProposal).filter_by(category="impact", status="pending").delete()

            for item_id, count in counts.items():
                tier = _percentile_tier(count, p25, p75)
                sess.add(TagProposal(
                    item_id=item_id,
                    tag_name=tier,
                    confidence=1.0,
                    source_item_keys="[]",
                    status="pending",
                    low_confidence=False,
                    is_new_tag=False,
                    generated_by="semantic_scholar",
                    category="impact",
                    created_at=datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S"),
                ))
                logger.info("impact tier: item %d → %s (citations=%d)", item_id, tier, count)
            sess.commit()
            result["impact_written"] = len(counts)
            logger.info(
                "citation enrichment done: found=%d not_found=%d p25=%d p75=%d",
                result["found"],
                result["not_found"],
                p25,
                p75,
            )

    sim.close()
    if progress_queue:
        progress_queue.put({
            "phase": "done",
            "current": total,
            "total": total,
            "title": "",
            "message": f"found={result['found']} not_found={result['not_found']}",
        })
    return result
