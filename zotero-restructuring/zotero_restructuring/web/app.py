"""
web/app.py — FastAPI application for the simplified zotero-restructuring tool.

Two pages:
    /        Page 1 — Tag Update: paths, Import & Normalize (ingest zotero.sqlite
             + import approved tags from a generatedtags DB), Backup, Apply.
    /stats   Page 2 — Stats & Network: tag distribution + co-occurrence graph,
             with an original / post-merge (original + approved proposals) toggle.

The sim DB (``zotero_restructure.db``) is the only state.  The source
``zotero.sqlite`` is read once at import and only ever copied (never written) at
backup time; tags are written to Zotero solely via the local API at apply time.

Heavy apply/import deps (pyzotero) are imported lazily inside the
background-thread targets so the app and its tests start without the full
``web`` extra installed.
"""

from __future__ import annotations

import collections
import datetime
import logging
import shutil
import threading
from pathlib import Path

from fastapi import Body, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from ..config import SIMULATION_DB_PATH, get_zotero_sqlite_path
from ..models import ExcludedTag, SimItem, SimItemTag, SimTag, TagProposal
from ..simulation import SimulationDB
from ..tags import is_substantive_tag
from .graph import build_graph
from .stats import compute_stats

_HERE = Path(__file__).parent
_TEMPLATES_DIR = _HERE / "templates"
_STATIC_DIR = _HERE / "static"

logger = logging.getLogger("zotero_restructuring.web")

# ── In-memory log buffer (ring) exposed via /api/applog ───────────────────────

_LOG_BUFFER: collections.deque = collections.deque(maxlen=2000)


class _BufferHandler(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:
        _LOG_BUFFER.append({
            "ts": self.formatter.formatTime(record, "%Y-%m-%dT%H:%M:%S")
            if self.formatter else record.asctime,
            "level": record.levelname,
            "name": record.name,
            "msg": self.format(record),
        })


_buf_handler = _BufferHandler()
_buf_handler.setFormatter(logging.Formatter("%(message)s"))
_buf_handler.setLevel(logging.DEBUG)
logging.getLogger("zotero_restructuring").addHandler(_buf_handler)
logging.getLogger("zotero_restructuring").setLevel(logging.DEBUG)


# ── Small DB helpers ──────────────────────────────────────────────────────────

def _open(db_path: Path) -> SimulationDB:
    return SimulationDB(db_path=db_path)


def _item_current_tags(sess, item_id: int) -> list[str]:
    names = {t.tag_id: t.name for t in sess.query(SimTag).all()}
    out: list[str] = []
    for link in sess.query(SimItemTag).filter_by(item_id=item_id).all():
        name = names.get(link.tag_id)
        if name:
            out.append(name)
    return out


# ── App factory ──────────────────────────────────────────────────────────────

def create_app(db_path: Path | None = None) -> FastAPI:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )
    app = FastAPI(title="zotero-restructuring")

    templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))
    if _STATIC_DIR.exists():
        app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")

    app.state.db_path = Path(db_path) if db_path else SIMULATION_DB_PATH
    app.state.imported = False
    app.state.import_error = None
    app.state.import_running = False
    app.state.apply_running = False
    app.state.apply_done = False
    app.state.apply_error = None
    app.state.last_import_result = None

    try:
        if app.state.db_path.exists():
            sim = _open(app.state.db_path)
            if sim.get_meta() is not None:
                app.state.imported = True
            sim.close()
    except Exception:
        pass

    # ── Page routes ──────────────────────────────────────────────────────────

    def _page(name: str):
        async def _handler(request: Request):
            return templates.TemplateResponse(request, name)
        return _handler

    app.get("/")(_page("page1_update.html"))
    app.get("/stats")(_page("page2_stats.html"))

    # ── Graph / stats ────────────────────────────────────────────────────────

    @app.get("/api/graph")
    async def api_graph(space: str = "papers", db: str = "original", min_weight: int = 1):
        return JSONResponse(
            build_graph(app.state.db_path, space=space, db=db, min_weight=min_weight)
        )

    @app.get("/api/stats")
    async def api_stats(db: str = "original"):
        return JSONResponse(compute_stats(app.state.db_path, db=db))

    # ── Tags (autocomplete) ──────────────────────────────────────────────────

    @app.get("/api/tags")
    async def api_tags():
        sim = _open(app.state.db_path)
        try:
            with sim.session() as sess:
                names = sorted(t.name for t in sess.query(SimTag).all())
        finally:
            sim.close()
        return JSONResponse(names)

    # ── Excluded tags ─────────────────────────────────────────────────────────

    @app.get("/api/excluded-tags")
    async def api_excluded_tags():
        sim = _open(app.state.db_path)
        try:
            with sim.session() as sess:
                rows = sess.query(ExcludedTag).order_by(ExcludedTag.name).all()
                return JSONResponse(
                    [{"id": r.id, "name": r.name, "reason": r.reason} for r in rows]
                )
        finally:
            sim.close()

    @app.post("/api/excluded-tags")
    async def api_add_excluded_tag(payload: dict = Body(default={})):
        name = str(payload.get("name", "")).strip().lower()
        if not name:
            raise HTTPException(400, "name required")
        sim = _open(app.state.db_path)
        try:
            with sim.session() as sess:
                existing = sess.query(ExcludedTag).filter_by(name=name).first()
                if existing:
                    return JSONResponse(
                        {"id": existing.id, "name": existing.name, "reason": existing.reason}
                    )
                row = ExcludedTag(name=name, reason="user")
                sess.add(row)
                sess.commit()
                return JSONResponse({"id": row.id, "name": row.name, "reason": row.reason})
        finally:
            sim.close()

    @app.delete("/api/excluded-tags/{tag_id}")
    async def api_remove_excluded_tag(tag_id: int):
        sim = _open(app.state.db_path)
        try:
            with sim.session() as sess:
                row = sess.query(ExcludedTag).filter_by(id=tag_id).first()
                if not row:
                    raise HTTPException(404, "not found")
                sess.delete(row)
                sess.commit()
            return JSONResponse({"status": "deleted"})
        finally:
            sim.close()

    # ── Status ───────────────────────────────────────────────────────────────

    @app.get("/api/status")
    async def api_status():
        item_count = tag_count = 0
        any_approved = False
        try:
            sim = _open(app.state.db_path)
            try:
                meta = sim.get_meta()
                if meta is not None:
                    item_count, tag_count = meta.item_count, meta.tag_count
                with sim.session() as sess:
                    any_approved = (
                        sess.query(TagProposal)
                        .filter(TagProposal.status.in_(("approved", "edited", "applied")))
                        .count() > 0
                    )
            finally:
                sim.close()
        except Exception:
            pass
        return JSONResponse({
            "imported": app.state.imported,
            "import_running": app.state.import_running,
            "import_error": app.state.import_error,
            "apply_running": app.state.apply_running,
            "apply_done": app.state.apply_done,
            "apply_error": app.state.apply_error,
            "any_approved": any_approved,
            "item_count": item_count,
            "tag_count": tag_count,
            "default_zotero_path": str(get_zotero_sqlite_path()),
            "default_db_path": str(SIMULATION_DB_PATH),
        })

    @app.get("/api/import-summary")
    async def api_import_summary():
        r = app.state.last_import_result
        if r is None:
            return JSONResponse({})
        return JSONResponse({
            "items_matched": r.items_matched,
            "proposals_imported": r.proposals_imported,
            "skipped_existing": r.skipped_existing,
            "skipped_unmatched": r.skipped_unmatched,
            "errors": r.errors,
        })

    @app.get("/api/health")
    async def api_health():
        db_ok = False
        proposal_count = 0
        try:
            sim = _open(app.state.db_path)
            with sim.session() as sess:
                proposal_count = sess.query(TagProposal).count()
            sim.close()
            db_ok = True
        except Exception as exc:  # noqa: BLE001
            logger.warning("health check DB error: %s", exc)
        return JSONResponse({
            "status": "ok" if db_ok else "db_error",
            "imported": app.state.imported,
            "db_ok": db_ok,
            "proposal_count": proposal_count,
        })

    @app.get("/api/applog")
    async def api_applog(level: str = "INFO", n: int = 500):
        _levels = {"DEBUG": 10, "INFO": 20, "WARNING": 30, "ERROR": 40}
        min_lvl = _levels.get(level.upper(), 20)
        records = [r for r in _LOG_BUFFER if _levels.get(r["level"], 0) >= min_lvl]
        return JSONResponse(records[-n:])

    # ── Import & Normalize (ingest + import generated tags) ───────────────────

    @app.post("/api/import-tags")
    async def api_import_tags(payload: dict = Body(default={})):
        """Ingest zotero.sqlite into the sim DB and import approved generated tags."""
        if app.state.import_running:
            raise HTTPException(409, "import already running")
        zotero_path = payload.get("zotero_path") or None
        generated_db = payload.get("generated_db") or None
        if not generated_db:
            raise HTTPException(400, "generated_db is required")
        gen_path = Path(generated_db).expanduser()
        if not gen_path.exists():
            raise HTTPException(404, f"generatedtags DB not found: {gen_path}")

        def _run():
            app.state.import_running = True
            app.state.import_error = None
            try:
                from ..import_tags import import_tags

                zp = Path(zotero_path).expanduser() if zotero_path else None
                r = import_tags(
                    gen_path,
                    sim_db_path=app.state.db_path,
                    zotero_sqlite_path=zp,
                )
                app.state.last_import_result = r
                app.state.imported = True
                logger.info(
                    "import-tags: matched=%d imported=%d skipped_existing=%d unmatched=%d",
                    r.items_matched, r.proposals_imported,
                    r.skipped_existing, r.skipped_unmatched,
                )
            except Exception as exc:  # noqa: BLE001
                app.state.import_error = str(exc)
                app.state.imported = False
                logger.error("import-tags failed: %s", exc)
            finally:
                app.state.import_running = False

        threading.Thread(target=_run, daemon=True).start()
        return JSONResponse({"status": "started"})

    # ── Backup (copy source sqlite — never write into it) ─────────────────────

    @app.post("/api/backup")
    async def api_backup():
        sim = _open(app.state.db_path)
        try:
            meta = sim.get_meta()
        finally:
            sim.close()
        if meta is None or not meta.zotero_sqlite_path:
            raise HTTPException(409, "no imported library / source path on record")
        src = Path(meta.zotero_sqlite_path)
        if not src.exists():
            raise HTTPException(404, f"source sqlite not found: {src}")
        ts = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
        dst = src.with_name(src.name + f".bak.{ts}")
        shutil.copy2(src, dst)
        return JSONResponse({"backup_path": str(dst)})

    # ── Apply (background thread) ─────────────────────────────────────────────

    @app.post("/api/apply")
    async def api_apply():
        def _run():
            app.state.apply_running = True
            app.state.apply_error = None
            try:
                from ..apply import apply_changes

                apply_changes(app.state.db_path)
                app.state.apply_done = True
            except Exception as exc:  # noqa: BLE001
                app.state.apply_error = str(exc)
            finally:
                app.state.apply_running = False

        threading.Thread(target=_run, daemon=True).start()
        return JSONResponse({"status": "started"})

    return app


app = create_app()
