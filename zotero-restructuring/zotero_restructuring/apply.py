"""
apply.py — Phase 4: write approved tag proposals to the live Zotero library.

Reads ``status=approved`` proposals from ``zotero_restructure.db`` and adds the
proposed tags to each item via the local Zotero HTTP API (port 23119).  Only
tag tables are touched; items, collections, attachments, notes and creators are
never modified.  Applied proposals are marked ``status=applied`` and recorded in
the ChangeLog.  The operation is idempotent: already-applied proposals are
skipped and re-running adds no duplicate tags.

The Zotero client is injectable so the logic is testable without a running
Zotero instance.
"""

from __future__ import annotations

import datetime
import json
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Protocol

from .config import ZOTERO_LOCAL_API
from .models import ChangeLog, SimItem, TagProposal
from .simulation import SimulationDB


# ── Zotero client protocol ───────────────────────────────────────────────────

class ZoteroClient(Protocol):
    """Minimal interface for adding tags to a Zotero item."""

    def add_tags(self, item_key: str, tags: list[str]) -> None:
        """Add ``tags`` to the item identified by ``item_key`` (idempotent)."""
        ...


class LocalZoteroClient:
    """Talks to the local Zotero HTTP API via pyzotero, falling back to httpx.

    The local connector serves the Zotero Web API at
    ``http://localhost:23119/api`` with library id ``0`` for the local user
    library.  We fetch the item, merge tags, and PATCH it back.
    """

    def __init__(self, base_url: str = ZOTERO_LOCAL_API) -> None:
        self.base_url = base_url.rstrip("/")
        self._zot = None
        try:
            from pyzotero import zotero  # lazy

            self._zot = zotero.Zotero(
                library_id="0",
                library_type="user",
                local=True,
            )
        except Exception:
            self._zot = None

    def add_tags(self, item_key: str, tags: list[str]) -> None:
        if self._zot is not None:
            item = self._zot.item(item_key)
            existing = {t["tag"] for t in item["data"].get("tags", [])}
            new = [{"tag": t} for t in tags if t not in existing]
            if not new:
                return
            item["data"]["tags"] = item["data"].get("tags", []) + new
            self._zot.update_item(item)
            return
        self._add_tags_http(item_key, tags)

    def _add_tags_http(self, item_key: str, tags: list[str]) -> None:
        import httpx  # lazy

        url = f"{self.base_url}/api/users/0/items/{item_key}"
        with httpx.Client(timeout=30.0) as client:
            resp = client.get(url)
            resp.raise_for_status()
            item = resp.json()
            existing = {t["tag"] for t in item["data"].get("tags", [])}
            new = [{"tag": t} for t in tags if t not in existing]
            if not new:
                return
            item["data"]["tags"] = item["data"].get("tags", []) + new
            version = item.get("version") or item["data"].get("version")
            client.patch(
                url,
                json={"tags": item["data"]["tags"]},
                headers={"If-Unmodified-Since-Version": str(version)},
            ).raise_for_status()


# ── Result ───────────────────────────────────────────────────────────────────

@dataclass
class ApplyResult:
    items_updated: int = 0
    tags_added: int = 0
    proposals_applied: int = 0
    proposals_skipped: int = 0
    errors: list[str] = field(default_factory=list)


# ── Apply entry point ────────────────────────────────────────────────────────

def apply_changes(
    db_path: Path,
    *,
    client: ZoteroClient | None = None,
    progress_cb: Callable[[int, int, str], None] | None = None,
) -> ApplyResult:
    """Apply all approved proposals in ``db_path`` to the Zotero library.

    Parameters
    ----------
    db_path:
        Path to ``zotero_restructure.db``.
    client:
        A :class:`ZoteroClient`.  Defaults to :class:`LocalZoteroClient`.
    progress_cb:
        Optional callback ``(current, total, item_title)``.
    """
    if client is None:
        client = LocalZoteroClient()

    result = ApplyResult()
    sim = SimulationDB(db_path=db_path)

    with sim.session() as sess:
        approved = (
            sess.query(TagProposal)
            .filter_by(status="approved")
            .order_by(TagProposal.item_id)
            .all()
        )

        # Group approved proposals by item so we make one API call per item.
        by_item: dict[int, list[TagProposal]] = defaultdict(list)
        for prop in approved:
            by_item[prop.item_id].append(prop)

        total = len(by_item)
        for idx, (item_id, props) in enumerate(by_item.items(), start=1):
            item = sess.get(SimItem, item_id)
            if item is None:
                result.errors.append(f"item {item_id}: not found in sim DB")
                continue

            tags = [p.effective_tag for p in props]
            if progress_cb is not None:
                progress_cb(idx, total, item.title or item.key)

            try:
                client.add_tags(item.key, tags)
            except Exception as exc:
                for p in props:
                    p.status = "failed"
                result.errors.append(f"item {item.key}: {exc}")
                sess.flush()
                continue

            now = datetime.datetime.now(datetime.timezone.utc).isoformat()
            for p in props:
                p.status = "applied"
                p.applied_at = now
                result.proposals_applied += 1
            sess.add(ChangeLog(
                timestamp=now,
                entity_type="item",
                entity_id=item_id,
                operation="apply_tags",
                field_name="tags",
                old_value=None,
                new_value=json.dumps(tags),
                description=f"Added tags {tags} to item {item.key}",
            ))
            result.items_updated += 1
            result.tags_added += len(tags)
            sess.flush()

        # Count proposals skipped because they were already applied earlier.
        result.proposals_skipped = (
            sess.query(TagProposal).filter_by(status="applied").count()
            - result.proposals_applied
        )
        sess.commit()

    sim.close()
    return result
