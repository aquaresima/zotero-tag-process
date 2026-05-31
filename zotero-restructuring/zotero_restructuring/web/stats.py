"""
web/stats.py — Tag distribution statistics for Page 2 (Chart.js).

Computes:
- Tag frequency histogram (counts per tag, for a log-log frequency plot).
- Zipf fit: alpha and R^2 from a linear regression on log(rank) vs log(frequency).
- Summary table: totals, items-with-zero-tags, mean/median tags per item,
  top-20 tags, and a junk-tag flag.

Uses numpy when available; falls back to a pure-Python regression otherwise so
the stats page works even without the ``web`` extra fully installed.
"""

from __future__ import annotations

import math
from collections import Counter
from pathlib import Path

from ..models import SimItem, SimItemTag, SimTag, TagProposal
from ..simulation import SimulationDB
from ..tags import is_junk_tag, is_substantive_tag


def _linregress(xs: list[float], ys: list[float]) -> tuple[float, float]:
    """Return (slope, r_squared) for a simple linear regression."""
    n = len(xs)
    if n < 2:
        return 0.0, 0.0
    mx = sum(xs) / n
    my = sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    if sxx == 0:
        return 0.0, 0.0
    slope = sxy / sxx
    intercept = my - slope * mx
    ss_tot = sum((y - my) ** 2 for y in ys)
    ss_res = sum((y - (slope * x + intercept)) ** 2 for x, y in zip(xs, ys))
    r2 = 1.0 - ss_res / ss_tot if ss_tot > 0 else 0.0
    return slope, r2


def compute_stats(db_path: Path, db: str = "original") -> dict:
    """Compute the full statistics payload for Page 2.

    When db="post", includes pending/approved/edited proposals in tag counts
    to show the effect of applying the proposals.
    """
    sim = SimulationDB(db_path=db_path)
    try:
        with sim.session() as sess:
            tag_names = {t.tag_id: t.name for t in sess.query(SimTag).all()}
            item_ids = [i.item_id for i in sess.query(SimItem).all()]

            counts: Counter[str] = Counter()
            per_item: dict[int, int] = {iid: 0 for iid in item_ids}

            # Count original tags
            for link in sess.query(SimItemTag).all():
                name = tag_names.get(link.tag_id)
                if name is None:
                    continue
                counts[name] += 1
                if is_substantive_tag(name) and link.item_id in per_item:
                    per_item[link.item_id] += 1

            # When db="post", also count pending/approved/edited proposals (category="general" only)
            if db == "post":
                proposals = sess.query(TagProposal).filter(
                    TagProposal.category == "general",
                    TagProposal.status.in_(("pending", "approved", "edited"))
                ).all()
                for p in proposals:
                    tag_name = p.effective_tag
                    counts[tag_name] += 1
                    if tag_name not in tag_names.values():  # virtual tag from proposal
                        if p.item_id in per_item:
                            per_item[p.item_id] += 1
    finally:
        sim.close()

    total_items = len(item_ids)
    total_tags = len(tag_names)
    freqs = sorted(counts.values(), reverse=True)

    # Zipf fit on log(rank) vs log(frequency)
    xs = [math.log(r + 1) for r in range(len(freqs))]
    ys = [math.log(f) for f in freqs]
    slope, r2 = _linregress(xs, ys)
    alpha = -slope

    # Histogram: frequency -> number of tags having that frequency
    hist = Counter(freqs)
    histogram = [
        {"frequency": f, "tag_count": c}
        for f, c in sorted(hist.items())
    ]

    tag_counts = sorted(per_item.values())
    items_zero = sum(1 for v in tag_counts if v == 0)
    mean_tags = sum(tag_counts) / total_items if total_items else 0.0
    median_tags = (
        tag_counts[len(tag_counts) // 2] if tag_counts else 0
    )

    top20 = [
        {
            "tag": name,
            "count": cnt,
            "junk": is_junk_tag(name) or not is_substantive_tag(name),
        }
        for name, cnt in counts.most_common(20)
    ]

    return {
        "summary": {
            "total_tags": total_tags,
            "total_items": total_items,
            "items_zero_tags": items_zero,
            "mean_tags_per_item": round(mean_tags, 2),
            "median_tags_per_item": median_tags,
        },
        "zipf": {"alpha": round(alpha, 3), "r_squared": round(r2, 3)},
        "histogram": histogram,
        "rank_frequency": [
            {"rank": r + 1, "frequency": f} for r, f in enumerate(freqs)
        ],
        "top_tags": top20,
    }
