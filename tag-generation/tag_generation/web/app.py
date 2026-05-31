"""
web/app.py — FastAPI app for the tag-generation tool.

Two pages:
    /        Page 1 — setup: paths, worker settings, Import+Clean, Generate,
             SSE progress bar.
    /review  Page 2 — real-time review: papers sorted by average confidence
             descending, confidence slider, per-tag approve/reject, per-paper
             approve-all / unapprove-all, bulk approve, 20/page pagination.

The generatedtags DB is the only state.  It is opened in WAL mode at ingest
time so the background Ollama writer and the per-request review readers do not
block each other.  Heavy deps (httpx via the worker) are imported lazily inside
the background-thread targets.
"""

from __future__ import annotations

import asyncio
import json
import logging
import queue
import threading
from pathlib import Path

from fastapi import Body, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.templating import Jinja2Templates

from ..config import (
    OLLAMA_BASE_URL,
    OLLAMA_MODEL,
    TAG_VOCAB_SIZE,
    WORKER_BATCH_SIZE,
    WORKER_CONFIDENCE_THRESHOLD,
    WORKER_MIN_TAGS,
    default_generatedtags_path,
    ensure_shared_on_path,
    get_zotero_sqlite_path,
)

ensure_shared_on_path()

from zotero_restructuring.models import (  # noqa: E402
    SimItem,
    SimItemTag,
    SimTag,
    TagProposal,
)
from zotero_restructuring.simulation import SimulationDB  # noqa: E402

_HERE = Path(__file__).parent
_TEMPLATES_DIR = _HERE / "templates"
_PAGE_SIZE = 20

logger = logging.getLogger("tag_generation.web")

_DECIDED = {"approved", "rejected", "applied", "edited"}


def _open(db_path: Path) -> SimulationDB:
    from ..ingest import allow_output_path

    allow_output_path(db_path)
    return SimulationDB(db_path=db_path)


def _item_authors(item: "SimItem") -> list[str]:
    try:
        return list(json.loads(item.creators_json or "[]"))
    except (TypeError, ValueError):
        return []


def _item_abstract(item: "SimItem") -> str:
    try:
        meta = json.loads(item.metadata_json or "{}")
    except (TypeError, ValueError):
        meta = {}
    return meta.get("abstractNote") or meta.get("abstract") or ""


def _item_year(item: "SimItem") -> str | None:
    return item.date[:4] if item.date else None


def _item_current_tags(sess, item_id: int) -> list[str]:
    names = {t.tag_id: t.name for t in sess.query(SimTag).all()}
    out: list[str] = []
    for link in sess.query(SimItemTag).filter_by(item_id=item_id).all():
        name = names.get(link.tag_id)
        if name:
            out.append(name)
    return out


def create_app(db_path: Path | None = None) -> FastAPI:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )
    app = FastAPI(title="tag-generation")
    templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))

    app.state.db_path = Path(db_path) if db_path else default_generatedtags_path()
    app.state.imported = False
    app.state.import_error = None
    app.state.import_running = False
    app.state.worker_running = False
    app.state.worker_done = False
    app.state.worker_error = None
    app.state.progress_queue = queue.Queue()
    app.state.last_result = None

    # Treat an existing DB with a meta row as already imported.
    try:
        if app.state.db_path.exists():
            sim = _open(app.state.db_path)
            if sim.get_meta() is not None:
                app.state.imported = True
            with sim.session() as sess:
                if sess.query(TagProposal).count() > 0:
                    app.state.worker_done = True
            sim.close()
    except Exception:
        pass

    # ── Pages ────────────────────────────────────────────────────────────────

    @app.get("/")
    async def page_setup(request: Request):
        return templates.TemplateResponse(request, "page1_setup.html")

    @app.get("/review")
    async def page_review(request: Request):
        return templates.TemplateResponse(request, "page2_review.html")

    # ── Status ───────────────────────────────────────────────────────────────

    @app.get("/api/status")
    async def api_status():
        item_count = tag_count = 0
        try:
            sim = _open(app.state.db_path)
            meta = sim.get_meta()
            if meta is not None:
                item_count, tag_count = meta.item_count, meta.tag_count
            sim.close()
        except Exception:
            pass
        return JSONResponse({
            "imported": app.state.imported,
            "import_running": app.state.import_running,
            "import_error": app.state.import_error,
            "worker_running": app.state.worker_running,
            "worker_done": app.state.worker_done,
            "worker_error": app.state.worker_error,
            "item_count": item_count,
            "tag_count": tag_count,
            "db_path": str(app.state.db_path),
            "default_zotero_path": str(get_zotero_sqlite_path()),
            "default_output_path": str(default_generatedtags_path()),
            "settings": {
                "ollama_model": OLLAMA_MODEL,
                "ollama_base_url": OLLAMA_BASE_URL,
                "batch_size": WORKER_BATCH_SIZE,
                "vocab_size": TAG_VOCAB_SIZE,
                "min_tags": WORKER_MIN_TAGS,
                "confidence_threshold": WORKER_CONFIDENCE_THRESHOLD,
            },
        })

    # ── Import & Clean (background thread) ────────────────────────────────────

    @app.post("/api/import")
    async def api_import(payload: dict = Body(default={})):
        if app.state.import_running:
            raise HTTPException(409, "import already running")
        zotero_path = payload.get("zotero_path") or None
        output_path = payload.get("output_path") or None
        if output_path:
            app.state.db_path = Path(output_path).expanduser()

        def _run():
            app.state.import_running = True
            app.state.import_error = None
            try:
                from ..ingest import ingest as run_ingest

                src = (
                    Path(zotero_path).expanduser()
                    if zotero_path else get_zotero_sqlite_path()
                )
                run_ingest(src, app.state.db_path)
                app.state.imported = True
                app.state.worker_done = False
            except Exception as exc:  # noqa: BLE001
                app.state.import_error = str(exc)
                app.state.imported = False
                logger.error("import failed: %s", exc)
            finally:
                app.state.import_running = False

        threading.Thread(target=_run, daemon=True).start()
        return JSONResponse({"status": "started"})

    # ── Generate (background thread + SSE) ────────────────────────────────────

    @app.post("/api/generate")
    async def api_generate(payload: dict = Body(default={})):
        if not app.state.imported:
            raise HTTPException(409, "import must complete before generating")
        if app.state.worker_running:
            raise HTTPException(409, "generation already running")

        model = payload.get("ollama_model") or OLLAMA_MODEL
        base_url = payload.get("ollama_base_url") or OLLAMA_BASE_URL
        batch_size = int(payload.get("batch_size") or WORKER_BATCH_SIZE)
        vocab_size = int(payload.get("vocab_size") or TAG_VOCAB_SIZE)
        min_tags = int(payload.get("min_tags") or WORKER_MIN_TAGS)

        app.state.progress_queue = queue.Queue()
        q = app.state.progress_queue

        def _run():
            app.state.worker_running = True
            app.state.worker_error = None
            try:
                from ..worker import run_worker

                r = run_worker(
                    app.state.db_path,
                    progress_queue=q,
                    ollama_model=model,
                    ollama_base_url=base_url,
                    batch_size=batch_size,
                    vocab_size=vocab_size,
                    min_tags=min_tags,
                )
                app.state.worker_done = True
                app.state.last_result = r
            except Exception as exc:  # noqa: BLE001
                app.state.worker_error = str(exc)
                q.put({"phase": "error", "current": 0, "total": 0,
                       "item_title": "", "message": str(exc)})
                logger.error("generation failed: %s", exc)
            finally:
                app.state.worker_running = False

        threading.Thread(target=_run, daemon=True).start()
        return JSONResponse({"status": "started"})

    @app.get("/api/progress")
    async def api_progress():
        q = app.state.progress_queue

        async def _gen():
            loop = asyncio.get_event_loop()
            while True:
                try:
                    event = await loop.run_in_executor(None, q.get, True, 1.0)
                except Exception:
                    yield ": keep-alive\n\n"
                    continue
                yield f"data: {json.dumps(event)}\n\n"
                if event.get("phase") in ("done", "error"):
                    break

        return StreamingResponse(_gen(), media_type="text/event-stream")

    # ── Review ───────────────────────────────────────────────────────────────

    def _counts(sess) -> dict:
        return {
            "pending": sess.query(TagProposal).filter_by(status="pending").count(),
            "approved": sess.query(TagProposal)
            .filter(TagProposal.status.in_(("approved", "edited"))).count(),
            "rejected": sess.query(TagProposal).filter_by(status="rejected").count(),
        }

    @app.get("/api/proposals")
    async def api_proposals(page: int = 1):
        sim = _open(app.state.db_path)
        try:
            with sim.session() as sess:
                props = sess.query(TagProposal).all()
                by_item: dict[int, list[TagProposal]] = {}
                for p in props:
                    by_item.setdefault(p.item_id, []).append(p)

                # An item is fully reviewed when it has at least one general
                # proposal and none remain pending (approved papers disappear).
                ranked: list[tuple[int, float]] = []
                for iid, plist in by_item.items():
                    pending = [p for p in plist if p.status == "pending"]
                    if not pending:
                        continue
                    avg_conf = sum(p.confidence for p in plist) / len(plist)
                    ranked.append((iid, avg_conf))

                # Sort by average confidence descending (highest first).
                ranked.sort(key=lambda t: t[1], reverse=True)

                total = len(ranked)
                total_pages = max(1, (total + _PAGE_SIZE - 1) // _PAGE_SIZE)
                page_c = max(1, min(page, total_pages))
                window = ranked[(page_c - 1) * _PAGE_SIZE: page_c * _PAGE_SIZE]

                items_out = []
                for iid, avg in window:
                    item = sess.get(SimItem, iid)
                    if item is None:
                        continue
                    plist = sorted(
                        by_item[iid], key=lambda p: p.confidence, reverse=True
                    )
                    items_out.append({
                        "item_id": iid,
                        "title": item.title or item.key,
                        "authors": _item_authors(item),
                        "year": _item_year(item),
                        "abstract": _item_abstract(item),
                        "avg_confidence": round(avg, 3),
                        "current_tags": _item_current_tags(sess, iid),
                        "proposals": [
                            {
                                "id": p.id,
                                "tag_name": p.tag_name,
                                "confidence": round(p.confidence, 3),
                                "status": p.status,
                                "category": p.category,
                                "is_new_tag": p.is_new_tag,
                            }
                            for p in plist
                        ],
                    })
                counts = _counts(sess)
        finally:
            sim.close()
        return JSONResponse({
            "items": items_out,
            "page": page_c,
            "total_pages": total_pages,
            "counts": counts,
        })

    def _set_status(prop_id: int, status: str) -> dict:
        sim = _open(app.state.db_path)
        try:
            with sim.session() as sess:
                p = sess.get(TagProposal, prop_id)
                if p is None:
                    raise HTTPException(404, "proposal not found")
                if p.status == "applied":
                    raise HTTPException(409, "proposal already applied")
                p.status = status
                sess.commit()
                return {"id": p.id, "item_id": p.item_id,
                        "tag_name": p.tag_name, "status": p.status}
        finally:
            sim.close()

    @app.post("/api/proposal/{prop_id}/approve")
    async def api_approve(prop_id: int):
        return JSONResponse(_set_status(prop_id, "approved"))

    @app.post("/api/proposal/{prop_id}/reject")
    async def api_reject(prop_id: int):
        return JSONResponse(_set_status(prop_id, "rejected"))

    @app.post("/api/proposal/{prop_id}/unapprove")
    async def api_unapprove(prop_id: int):
        sim = _open(app.state.db_path)
        try:
            with sim.session() as sess:
                p = sess.get(TagProposal, prop_id)
                if p is None:
                    raise HTTPException(404, "proposal not found")
                if p.status == "applied":
                    raise HTTPException(409, "cannot unapprove an applied proposal")
                p.status = "pending"
                sess.commit()
                return JSONResponse({"id": p.id, "status": p.status})
        finally:
            sim.close()

    @app.post("/api/item/{item_id}/approve-all")
    async def api_item_approve_all(item_id: int, threshold: float = 0.7):
        sim = _open(app.state.db_path)
        try:
            with sim.session() as sess:
                pending = (
                    sess.query(TagProposal)
                    .filter_by(item_id=item_id, status="pending")
                    .filter(TagProposal.confidence >= threshold)
                    .all()
                )
                for p in pending:
                    p.status = "approved"
                count = len(pending)
                sess.commit()
        finally:
            sim.close()
        return JSONResponse({"approved": count})

    @app.post("/api/item/{item_id}/unapprove-all")
    async def api_item_unapprove_all(item_id: int):
        sim = _open(app.state.db_path)
        try:
            with sim.session() as sess:
                rows = (
                    sess.query(TagProposal)
                    .filter_by(item_id=item_id)
                    .filter(TagProposal.status.in_(("approved", "edited")))
                    .all()
                )
                for p in rows:
                    p.status = "pending"
                count = len(rows)
                sess.commit()
        finally:
            sim.close()
        return JSONResponse({"reset": count})

    @app.post("/api/proposals/bulk-approve")
    async def api_bulk_approve(threshold: float = 0.7):
        sim = _open(app.state.db_path)
        try:
            with sim.session() as sess:
                pending = (
                    sess.query(TagProposal)
                    .filter_by(status="pending")
                    .filter(TagProposal.confidence >= threshold)
                    .all()
                )
                for p in pending:
                    p.status = "approved"
                count = len(pending)
                sess.commit()
        finally:
            sim.close()
        return JSONResponse({"approved": count})

    @app.get("/api/health")
    async def api_health():
        db_ok = False
        proposal_count = pending_count = 0
        try:
            sim = _open(app.state.db_path)
            with sim.session() as sess:
                proposal_count = sess.query(TagProposal).count()
                pending_count = (
                    sess.query(TagProposal).filter_by(status="pending").count()
                )
            sim.close()
            db_ok = True
        except Exception as exc:  # noqa: BLE001
            logger.warning("health DB error: %s", exc)
        return JSONResponse({
            "status": "ok" if db_ok else "db_error",
            "imported": app.state.imported,
            "worker_running": app.state.worker_running,
            "worker_done": app.state.worker_done,
            "db_ok": db_ok,
            "proposal_count": proposal_count,
            "pending_count": pending_count,
        })

    return app


app = create_app()
