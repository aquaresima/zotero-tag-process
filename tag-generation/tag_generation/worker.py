"""
worker.py — Ollama batch tagger for the tag-generation tool.

Adapted from ``zotero_restructuring.worker``.  Key differences:

* Processes **ALL** papers in the library — no tag-count filter.  Every paper
  gets fresh proposals (subject to idempotency below).
* Targets 10–15 tags per paper (freeform + vocab + methods + fields).
* Writes proposals to the DB **after each batch** (real-time), so the review
  page can populate as generation proceeds.  SQLite WAL mode keeps the writer
  and the review-page reader from blocking each other.
* Idempotency on restart:
    - Papers with any approved/rejected proposal  → skipped entirely.
    - Papers with only pending proposals          → pending rows overwritten.
    - Papers with no proposals                     → generated normally.

The shared ORM models, ``normalize_tag``, and the ``SimulationDB`` wrapper are
imported from the sibling ``zotero-restructuring`` package (resolved onto
``sys.path`` by :func:`tag_generation.config.ensure_shared_on_path`).

Heavy dependency (httpx) is imported lazily so the module — and its tests —
import cleanly without the ``web`` extra installed.
"""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from queue import Queue
from typing import TYPE_CHECKING

os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")

from .config import (
    LLM_BACKEND,
    OLLAMA_BASE_URL,
    OLLAMA_MODEL,
    TAG_VOCAB_SIZE,
    WORKER_BATCH_SIZE,
    WORKER_MIN_TAGS,
    WORKER_TARGET_TAGS,
    ensure_shared_on_path,
)

# Bring the shared zotero-restructuring modules onto sys.path before importing.
ensure_shared_on_path()

from zotero_restructuring.models import (  # noqa: E402
    ExcludedTag,
    SimItem,
    SimItemTag,
    SimTag,
    TagProposal,
)
from zotero_restructuring.simulation import SimulationDB  # noqa: E402
from zotero_restructuring.tags import normalize_tag  # noqa: E402
from zotero_restructuring.taxonomy import load_taxonomy  # noqa: E402

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

logger = logging.getLogger("tag_generation.worker")

# Reuse the junk/substantive heuristics (moved into the shared tags module
# when the shared worker.py was removed).
from zotero_restructuring.tags import is_substantive_tag  # noqa: E402

_TAXONOMY = load_taxonomy()
_SYSTEM_PROMPT = _TAXONOMY.rendered_prompt()
_METHOD_VOCAB = _TAXONOMY.method_vocab
_FIELD_TAXONOMY = _TAXONOMY.field_taxonomy


# ── Progress events ────────────────────────────────────────────────────────────

@dataclass
class ProgressEvent:
    """A single progress update emitted by the worker."""

    current: int
    total: int
    item_title: str
    phase: str = "generating"  # "generating" | "proposals_ready" | "done" | "error"
    message: str = ""

    def to_dict(self) -> dict:
        return {
            "current": self.current,
            "total": self.total,
            "item_title": self.item_title,
            "phase": self.phase,
            "message": self.message,
        }


@dataclass
class WorkerResult:
    """Summary of a worker run."""

    items_processed: int = 0
    proposals_written: int = 0
    low_confidence_items: int = 0
    skipped_decided: int = 0
    errors: list[str] = field(default_factory=list)


# ── Prompt building + response parsing (adapted from shared worker) ───────────

def _build_messages(
    papers: list[tuple[int, str, str, list[str]]],
    vocab: list[str],
) -> tuple[str, str]:
    """Return (system_prompt, user_message) for a batch.

    Each paper tuple is (item_id, title, abstract, existing_tags).
    """
    vocab_str = ", ".join(vocab)
    field_str = "\n".join(f"  - {f}" for f in _FIELD_TAXONOMY)
    papers_block = "\n\n".join(
        f"item_id={iid}\nTitle: {title}\nAbstract: {abstract or '(none)'}\n"
        f"Already existing tags: {', '.join(existing) if existing else '(none)'}"
        for iid, title, abstract, existing in papers
    )
    user_msg = (
        f"Tag vocabulary:\n{vocab_str}\n\n"
        f"Field taxonomy:\n{field_str}\n\n"
        f"Aim for {WORKER_MIN_TAGS}-{WORKER_TARGET_TAGS} tags total per paper "
        f"across all categories.\n\n"
        f"Papers:\n{papers_block}"
    )
    return _SYSTEM_PROMPT, user_msg


def _extract_json_array(text: str) -> str:
    """Extract the first JSON array substring from model output."""
    text = re.sub(r"```(?:json)?\s*", "", text).strip()
    start = text.find("[")
    end = text.rfind("]")
    if start == -1 or end == -1 or end < start:
        return "[]"
    return text[start : end + 1]


def _parse_scored(raw: list, default_score: float) -> list[tuple[str, float]]:
    out: list[tuple[str, float]] = []
    for t in raw:
        if isinstance(t, dict):
            name = str(t.get("name", "")).strip()
            score = float(t.get("score", default_score))
            if name:
                out.append((name, min(max(score, 0.0), 1.0)))
        elif isinstance(t, str) and t.strip():
            out.append((t.strip(), default_score))
    return out


def parse_batch_response(text: str) -> dict[int, dict]:
    """Parse the JSON response from the LLM backend.

    Returns {item_id: {"tags": [(name, score)], "methods": [...], "fields": [...]}}.
    Handles JSON arrays, single bare objects, and objects wrapping a list under a
    known key, plus the two-pass freeform/vocab schema and legacy flat strings.
    """
    root = None
    try:
        root = json.loads(text)
    except Exception:
        pass
    if root is None:
        try:
            root = json.loads(_extract_json_array(text))
        except Exception:
            return {}

    if isinstance(root, list):
        data = root
    elif isinstance(root, dict):
        if "item_id" in root:
            data = [root]
        else:
            for key in ("results", "items", "papers", "data"):
                if key in root and isinstance(root[key], list):
                    data = root[key]
                    break
            else:
                data = [root]
    else:
        return {}

    result: dict[int, dict] = {}
    for entry in data:
        if not isinstance(entry, dict):
            continue
        try:
            iid = int(entry["item_id"])
        except (KeyError, ValueError, TypeError):
            continue

        tags: list[tuple[str, float]] = []
        tags += _parse_scored(entry.get("freeform_tags", []), 0.85)
        tags += _parse_scored(entry.get("vocab_tags", []), 0.9)
        if not tags:
            tags += _parse_scored(entry.get("tags", []), 0.9)

        methods = _parse_scored(entry.get("methods", []), 0.9)
        fields = _parse_scored(entry.get("fields", []), 0.9)
        result[iid] = {"tags": tags, "methods": methods, "fields": fields}
    return result


def _ollama_batch(
    system_prompt: str, user_msg: str, model: str, base_url: str
) -> dict[int, dict]:
    try:
        import httpx
    except ImportError:
        return {}
    try:
        resp = httpx.post(
            f"{base_url}/api/chat",
            json={
                "model": model,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_msg},
                ],
                "stream": False,
                "options": {"temperature": 0.1},
                "format": "json",
            },
            timeout=180.0,
        )
        resp.raise_for_status()
        text = resp.json()["message"]["content"]
        logger.debug("OLLAMA RAW RESPONSE:\n%s", text[:2000])
        parsed = parse_batch_response(text)
        for iid, d in parsed.items():
            logger.info(
                "item_id=%d  tags=%d  methods=%d  fields=%d",
                iid, len(d["tags"]), len(d["methods"]), len(d["fields"]),
            )
        return parsed
    except Exception as exc:  # noqa: BLE001
        logger.warning("ollama batch failed: %s", exc)
        return {}


def _anthropic_batch(
    system_prompt: str, user_msg: str, model: str
) -> dict[int, dict]:
    try:
        import anthropic

        client = anthropic.Anthropic()
        resp = client.messages.create(
            model=model,
            max_tokens=2048,
            system=system_prompt,
            messages=[{"role": "user", "content": user_msg}],
        )
        text = resp.content[0].text  # type: ignore[attr-defined]
        return parse_batch_response(text)
    except Exception as exc:  # noqa: BLE001
        logger.warning("anthropic batch failed: %s", exc)
        return {}


def llm_batch(
    papers: list[tuple[int, str, str, list[str]]],
    vocab: list[str],
    *,
    backend: str = "ollama",
    model: str = "llama3.1:latest",
    ollama_base_url: str = "http://localhost:11434",
) -> dict[int, dict]:
    """Tag a batch of papers using the configured LLM backend."""
    system_prompt, user_msg = _build_messages(papers, vocab)
    if backend == "ollama":
        return _ollama_batch(system_prompt, user_msg, model, ollama_base_url)
    if backend == "anthropic":
        return _anthropic_batch(system_prompt, user_msg, model)
    raise ValueError(f"Unknown LLM backend: {backend!r}")


# ── DB helpers ────────────────────────────────────────────────────────────────

def _item_abstract(item: "SimItem") -> str:
    try:
        meta = json.loads(item.metadata_json or "{}")
    except (TypeError, ValueError):
        meta = {}
    return meta.get("abstractNote") or meta.get("abstract") or ""


def load_excluded_set(sess: "Session") -> set[str]:
    return {row.name.lower() for row in sess.query(ExcludedTag).all()}


def _build_item_tag_index(
    sess: "Session", excluded: set[str]
) -> dict[int, list[str]]:
    """Return item_id -> substantive existing tag names (for the prompt)."""
    tag_names = {t.tag_id: t.name for t in sess.query(SimTag).all()}
    out: dict[int, list[str]] = {}
    for link in sess.query(SimItemTag).all():
        name = tag_names.get(link.tag_id)
        if name is None or not is_substantive_tag(name, excluded):
            continue
        out.setdefault(link.item_id, []).append(name)
    return out


def _load_top_tags(sess: "Session", n: int) -> list[str]:
    """Return the top-N tags by document frequency, excluding excluded tags."""
    from sqlalchemy import func as sqlfunc

    excluded = {r.name.lower() for r in sess.query(ExcludedTag).all()}
    rows = (
        sess.query(SimTag.name, sqlfunc.count(SimItemTag.item_id))
        .join(SimItemTag, SimTag.tag_id == SimItemTag.tag_id, isouter=True)
        .group_by(SimTag.tag_id)
        .order_by(sqlfunc.count(SimItemTag.item_id).desc())
        .all()
    )
    out: list[str] = []
    for name, _ in rows:
        if name.lower() not in excluded:
            out.append(name)
        if len(out) >= n:
            break
    return out


def _decided_item_ids(sess: "Session") -> set[int]:
    """Item ids that have at least one approved/rejected/applied/edited proposal."""
    rows = (
        sess.query(TagProposal.item_id)
        .filter(TagProposal.status.in_(("approved", "rejected", "applied", "edited")))
        .distinct()
        .all()
    )
    return {r[0] for r in rows}


def _emit(queue: "Queue | None", event: ProgressEvent) -> None:
    if queue is not None:
        queue.put(event.to_dict())


# ── Worker entry point ─────────────────────────────────────────────────────────

def run_worker(
    db_path: Path,
    *,
    progress_queue: "Queue | None" = None,
    llm_backend: str | None = None,
    ollama_model: str | None = None,
    ollama_base_url: str | None = None,
    batch_size: int | None = None,
    vocab_size: int | None = None,
    min_tags: int | None = None,
    llm_fn=None,
) -> WorkerResult:
    """Run the Ollama batch tagger against the generatedtags DB at ``db_path``.

    Processes ALL papers (subject to idempotency), writing proposals after each
    batch.  ``llm_fn`` is injectable for testing and must mirror the signature
    of :func:`llm_batch`.
    """
    _backend = llm_backend or LLM_BACKEND
    _model = ollama_model or OLLAMA_MODEL
    _url = ollama_base_url or OLLAMA_BASE_URL
    _batch = batch_size or WORKER_BATCH_SIZE
    _vocab_size = vocab_size or TAG_VOCAB_SIZE
    _min_tags = min_tags or WORKER_MIN_TAGS
    _call = llm_fn or llm_batch
    _generated_by = f"{_backend}_batch"

    from .ingest import allow_output_path
    allow_output_path(db_path)

    result = WorkerResult()
    sim = SimulationDB(db_path=db_path)
    meta = sim.get_meta()
    if meta is None:
        sim.close()
        raise RuntimeError(
            "No SessionMeta row in the generatedtags DB; run ingest first."
        )

    with sim.session() as sess:
        excluded = load_excluded_set(sess)
        item_tags = _build_item_tag_index(sess, excluded)
        vocab = _load_top_tags(sess, _vocab_size)
        existing_names = {t.name for t in sess.query(SimTag).all()}

        decided = _decided_item_ids(sess)
        all_items = sess.query(SimItem).order_by(SimItem.item_id).all()
        # Idempotency: skip items with human decisions; everyone else is fair game.
        todo = [i for i in all_items if i.item_id not in decided]
        result.skipped_decided = len(all_items) - len(todo)
        total = len(todo)

        for batch_start in range(0, total, _batch):
            batch = todo[batch_start : batch_start + _batch]
            batch_end = min(batch_start + _batch, total)
            _emit(progress_queue, ProgressEvent(
                batch_end, total, batch[0].title or "",
                phase="generating",
                message=f"Generating {batch_end}/{total}: {(batch[0].title or '')[:60]}",
            ))

            papers = [
                (it.item_id, it.title or "", _item_abstract(it),
                 item_tags.get(it.item_id, []))
                for it in batch
            ]
            try:
                batch_result = _call(
                    papers, vocab,
                    backend=_backend, model=_model, ollama_base_url=_url,
                )
            except Exception as exc:  # noqa: BLE001
                result.errors.append(f"batch {batch_start}: {exc}")
                logger.error("batch %d failed: %s", batch_start, exc)
                batch_result = {}

            # Clear any leftover pending rows for these items, then write fresh.
            batch_item_ids = [it.item_id for it in batch]
            (
                sess.query(TagProposal)
                .filter(TagProposal.item_id.in_(batch_item_ids))
                .filter(TagProposal.status == "pending")
                .delete(synchronize_session=False)
            )

            for it in batch:
                d = batch_result.get(it.item_id, {"tags": [], "methods": [], "fields": []})
                written = _write_item_proposals(
                    sess, it.item_id, d, existing_names, excluded,
                    _generated_by, _min_tags, result,
                )
                result.items_processed += 1
                result.proposals_written += written

            sess.commit()
            _emit(progress_queue, ProgressEvent(
                batch_end, total, "", phase="proposals_ready",
                message=f"{result.proposals_written} proposals so far",
            ))

    sim.record_worker_run({"tool": "tag-generation", "backend": _backend})
    _emit(progress_queue, ProgressEvent(
        total, total, "", phase="done",
        message=f"{result.proposals_written} proposals written",
    ))
    sim.close()
    return result


def _write_item_proposals(
    sess: "Session",
    item_id: int,
    d: dict,
    existing_names: set[str],
    excluded: set[str],
    generated_by: str,
    min_tags: int,
    result: WorkerResult,
) -> int:
    """Write all proposals for one item.  Returns the number written."""
    written_keys: set[tuple[int, str]] = set()
    count = 0

    # General tags (freeform + vocab), normalized.
    # Skip tags that look like field taxonomy terms (contain /) — they belong in fields.
    _field_set = set(_FIELD_TAXONOMY)
    general: list[tuple[str, float]] = []
    seen: set[str] = set()
    for raw_name, score in d.get("tags", []):
        if raw_name.strip() in _field_set:
            continue  # LLM misrouted a field term into general tags — skip
        norm = normalize_tag(raw_name)
        if not norm or norm.lower() in excluded or norm in seen:
            continue
        seen.add(norm)
        general.append((norm, score))

    low_conf = len(general) < min_tags
    if low_conf:
        result.low_confidence_items += 1

    for name, score in general:
        key = (item_id, name)
        if key in written_keys:
            continue
        sess.add(TagProposal(
            item_id=item_id,
            tag_name=name,
            confidence=score,
            source_item_keys=json.dumps([]),
            status="pending",
            low_confidence=low_conf,
            is_new_tag=name not in existing_names,
            generated_by=generated_by,
            category="general",
        ))
        written_keys.add(key)
        count += 1

    for name, score in d.get("methods", []):
        name = name.strip()
        if not name:
            continue
        key = (item_id, name)
        if key in written_keys:
            continue
        sess.add(TagProposal(
            item_id=item_id, tag_name=name, confidence=score,
            source_item_keys=json.dumps([]), status="pending",
            low_confidence=False, is_new_tag=name not in existing_names,
            generated_by=generated_by, category="method",
        ))
        written_keys.add(key)
        count += 1

    for name, score in d.get("fields", []):
        name = name.strip()
        if not name:
            continue
        key = (item_id, name)
        if key in written_keys:
            continue
        sess.add(TagProposal(
            item_id=item_id, tag_name=name, confidence=score,
            source_item_keys=json.dumps([]), status="pending",
            low_confidence=False, is_new_tag=name not in existing_names,
            generated_by=generated_by, category="field",
        ))
        written_keys.add(key)
        count += 1

    general_names = [n for n, _ in general]
    method_names  = [n for n, _ in d.get("methods", [])]
    field_names   = [n for n, _ in d.get("fields", [])]
    logger.info(
        "[item %d]%s\n  general (%d): %s\n  methods (%d): %s\n  fields  (%d): %s",
        item_id,
        " [LOW CONF]" if low_conf else "",
        len(general_names), ", ".join(general_names) or "(none)",
        len(method_names),  ", ".join(method_names)  or "(none)",
        len(field_names),   ", ".join(field_names)   or "(none)",
    )
    return count
