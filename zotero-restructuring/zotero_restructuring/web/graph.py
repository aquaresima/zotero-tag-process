"""
web/graph.py — Build sigma.js node/edge JSON from the simulation database.

Two graph spaces:

- **papers**: nodes = items, an edge connects two papers sharing >= 1 tag,
  weight = number of shared tags.
- **tags**: nodes = tags, an edge connects two tags co-occurring on >= 1 paper,
  weight = number of shared papers.

When ``db="new"`` the graph also reflects approved/edited tag proposals, so the
UI can visually compare before/after.  Node size is proportional to degree.
"""

from __future__ import annotations

from collections import defaultdict
from itertools import combinations
from pathlib import Path

from ..models import SimItem, SimItemTag, SimTag, TagProposal
from ..simulation import SimulationDB
from ..tags import is_substantive_tag


def _item_tag_map(
    db_path: Path, *, include_proposals: bool
) -> tuple[dict[int, set[str]], dict[int, dict]]:
    """Return (item_id -> set of tag names) and (item_id -> item info)."""
    sim = SimulationDB(db_path=db_path)
    try:
        with sim.session() as sess:
            tag_names = {t.tag_id: t.name for t in sess.query(SimTag).all()}
            items = {
                i.item_id: {"title": i.title or i.key, "key": i.key, "type": i.item_type}
                for i in sess.query(SimItem).all()
            }
            item_tags: dict[int, set[str]] = defaultdict(set)
            for link in sess.query(SimItemTag).all():
                name = tag_names.get(link.tag_id)
                if name and is_substantive_tag(name):
                    item_tags[link.item_id].add(name)

            if include_proposals:
                for p in sess.query(TagProposal).filter(
                    TagProposal.status.in_(("approved", "edited", "applied"))
                ).all():
                    item_tags[p.item_id].add(p.effective_tag)
    finally:
        sim.close()
    return item_tags, items


def build_paper_graph(db_path: Path, *, db: str = "original", min_weight: int = 1) -> dict:
    """Build the paper-space graph for sigma.js."""
    item_tags, items = _item_tag_map(db_path, include_proposals=(db == "new"))

    edges: dict[tuple[int, int], int] = defaultdict(int)
    ids = [iid for iid in item_tags if item_tags[iid]]
    for a, b in combinations(ids, 2):
        shared = len(item_tags[a] & item_tags[b])
        if shared:
            edges[(a, b)] = shared

    degree: dict[int, int] = defaultdict(int)
    edge_list = []
    for (a, b), w in edges.items():
        if w < min_weight:
            continue
        degree[a] += 1
        degree[b] += 1
        edge_list.append({"source": str(a), "target": str(b), "weight": w})

    nodes = [
        {
            "id": str(iid),
            "label": items[iid]["title"],
            "size": 1 + degree.get(iid, 0),
            "key": items[iid]["key"],
            "tags": sorted(item_tags[iid]),
        }
        for iid in ids
    ]
    return {"nodes": nodes, "edges": edge_list, "space": "papers"}


def build_tag_graph(db_path: Path, *, db: str = "original", min_weight: int = 1) -> dict:
    """Build the tag-space graph for sigma.js."""
    item_tags, _ = _item_tag_map(db_path, include_proposals=(db == "new"))

    tag_items: dict[str, set[int]] = defaultdict(set)
    for iid, tags in item_tags.items():
        for t in tags:
            tag_items[t].add(iid)

    edges: dict[tuple[str, str], int] = defaultdict(int)
    tags = sorted(tag_items)
    for ta, tb in combinations(tags, 2):
        shared = len(tag_items[ta] & tag_items[tb])
        if shared:
            edges[(ta, tb)] = shared

    degree: dict[str, int] = defaultdict(int)
    edge_list = []
    for (ta, tb), w in edges.items():
        if w < min_weight:
            continue
        degree[ta] += 1
        degree[tb] += 1
        edge_list.append({"source": ta, "target": tb, "weight": w})

    nodes = [
        {
            "id": t,
            "label": t,
            "size": 1 + degree.get(t, 0),
            "paper_count": len(tag_items[t]),
        }
        for t in tags
    ]
    return {"nodes": nodes, "edges": edge_list, "space": "tags"}


def build_graph(
    db_path: Path, *, space: str = "papers", db: str = "original", min_weight: int = 1
) -> dict:
    """Dispatch to the requested graph space."""
    if space == "tags":
        return build_tag_graph(db_path, db=db, min_weight=min_weight)
    return build_paper_graph(db_path, db=db, min_weight=min_weight)
