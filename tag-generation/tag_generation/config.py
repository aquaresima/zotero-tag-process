"""
config.py — Paths and worker settings for the tag-generation tool.

Only stdlib + python-dotenv here; never import application modules.

This package reuses several modules from the sibling ``zotero-restructuring``
package (the shared ORM models, ``tags.normalize_tag``, the simulation clone
logic, the taxonomy loader, and the Zotero reader/library).  The location of
that package is resolved here and prepended to ``sys.path`` so those modules
can be imported as ``from zotero_restructuring import ...``.
"""

from __future__ import annotations

import datetime
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

# ── Load .env from the project root (directory containing this package) ───────
_PROJECT_ROOT = Path(__file__).parent.parent
_ENV_FILE = _PROJECT_ROOT / ".env"
load_dotenv(dotenv_path=_ENV_FILE, override=False)


# ── Sibling zotero-restructuring package (shared modules) ─────────────────────

def get_zotero_restructuring_path() -> Path:
    """Return the path to the zotero-restructuring package root.

    Priority: ZOTERO_RESTRUCTURING_PATH env var >
    ~/.claude/lib/zotero-restructuring default.
    """
    raw = os.getenv("ZOTERO_RESTRUCTURING_PATH")
    if raw:
        return Path(raw).expanduser().resolve()
    return (Path.home() / ".claude" / "lib" / "zotero-restructuring").resolve()


def ensure_shared_on_path() -> Path:
    """Prepend the zotero-restructuring package root to sys.path.

    Returns the resolved package root.  Idempotent.
    """
    root = get_zotero_restructuring_path()
    root_str = str(root)
    if root_str not in sys.path:
        sys.path.insert(0, root_str)
    return root


# ── Source database (read-only) ───────────────────────────────────────────────

def get_zotero_sqlite_path() -> Path:
    """Return the path to the Zotero source database.

    Priority: ZOTERO_SQLITE_PATH env var > ~/Zotero/zotero.sqlite default.
    """
    raw = os.getenv("ZOTERO_SQLITE_PATH")
    if raw:
        return Path(raw).expanduser().resolve()
    return Path.home() / "Zotero" / "zotero.sqlite"


# ── Output database (the generatedtags DB) ────────────────────────────────────

DATA_DIR: Path = Path(os.getenv("TAG_GENERATION_DATA_DIR", str(Path.home() / "data")))


def default_generatedtags_path() -> Path:
    """Return the default output path for today's generatedtags DB."""
    today = datetime.date.today().isoformat()
    return DATA_DIR / f"zotero_generatedtags_{today}.sqlite"


# ── LLM backend / worker settings ─────────────────────────────────────────────

LLM_BACKEND: str = os.getenv("LLM_BACKEND", "ollama")
OLLAMA_BASE_URL: str = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
OLLAMA_MODEL: str = os.getenv("OLLAMA_MODEL", "llama3.1:latest")
HAIKU_MODEL: str = os.getenv("HAIKU_MODEL", "claude-haiku-4-5-20251001")

WORKER_BATCH_SIZE: int = int(os.getenv("WORKER_BATCH_SIZE", "5"))
TAG_VOCAB_SIZE: int = int(os.getenv("TAG_VOCAB_SIZE", "2000"))
WORKER_MIN_TAGS: int = int(os.getenv("WORKER_MIN_TAGS", "10"))
WORKER_TARGET_TAGS: int = int(os.getenv("WORKER_TARGET_TAGS", "15"))
WORKER_CONFIDENCE_THRESHOLD: float = float(
    os.getenv("WORKER_CONFIDENCE_THRESHOLD", "0.7")
)
