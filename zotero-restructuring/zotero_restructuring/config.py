"""
config.py — All constants, paths, and environment variable loading.

Never import application modules here; only stdlib and python-dotenv.
"""

import os
from pathlib import Path

from dotenv import load_dotenv

# ── Load .env from the project root (the directory containing this package) ──
_PROJECT_ROOT = Path(__file__).parent.parent
_ENV_FILE = _PROJECT_ROOT / ".env"
load_dotenv(dotenv_path=_ENV_FILE, override=False)

# ── Source database (read-only) ───────────────────────────────────────────────

def get_zotero_sqlite_path() -> Path:
    """Return the path to the Zotero source database.

    Priority: ZOTERO_SQLITE_PATH env var > ~/Zotero/zotero.sqlite default.
    """
    raw = os.getenv("ZOTERO_SQLITE_PATH")
    if raw:
        return Path(raw).expanduser().resolve()
    return Path.home() / "Zotero" / "zotero.sqlite"


# ── Write-allowed paths (whitelist enforced in simulation.py) ─────────────────

DATA_DIR: Path = _PROJECT_ROOT / "data"
# Single source of truth for the web app and apply step (renamed from
# simulation.sqlite).  After import, only this file is read.
SIMULATION_DB_PATH: Path = DATA_DIR / "zotero_restructure.db"
BACKUP_DIR: Path = DATA_DIR / "backup"

ALLOWED_WRITE_PATHS: tuple[Path, ...] = (
    DATA_DIR,       # any file under data/ is allowed
    BACKUP_DIR,
)


# ── Semantic search / embeddings (read-only at worker step) ───────────────────

def get_chroma_db_path() -> Path:
    """Return the ChromaDB persistent path.

    Priority: CHROMA_DB_PATH env var > ~/.zotero-chroma default.
    """
    raw = os.getenv("CHROMA_DB_PATH")
    if raw:
        return Path(raw).expanduser().resolve()
    return Path.home() / ".config" / "zotero-mcp" / "chroma_db"


CHROMA_COLLECTION: str = os.getenv("CHROMA_COLLECTION", "zotero_library")
EMBEDDING_MODEL: str = os.getenv("EMBEDDING_MODEL", "all-MiniLM-L6-v2")

# LLM backend: "ollama" (default, local) or "anthropic"
LLM_BACKEND: str = os.getenv("LLM_BACKEND", "ollama")

# Ollama settings (used when LLM_BACKEND=ollama)
OLLAMA_BASE_URL: str = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
OLLAMA_MODEL: str = os.getenv("OLLAMA_MODEL", "qwen2.5:7b")

# Anthropic / Claude Haiku (used when LLM_BACKEND=anthropic)
HAIKU_MODEL: str = os.getenv("HAIKU_MODEL", "claude-haiku-4-5-20251001")

# Local Zotero HTTP API (zotero connector / better-bibtex), used by apply.py.
ZOTERO_LOCAL_API: str = os.getenv(
    "ZOTERO_LOCAL_API", "http://localhost:23119"
)

# Worker defaults
WORKER_CONFIDENCE_THRESHOLD: float = float(os.getenv("WORKER_CONFIDENCE_THRESHOLD", "0.15"))
WORKER_TOP_K: int = int(os.getenv("WORKER_TOP_K", "20"))
WORKER_MIN_TAGS: int = int(os.getenv("WORKER_MIN_TAGS", "5"))
WORKER_LOW_CONF_MAX: float = 0.4
WORKER_BATCH_SIZE: int = int(os.getenv("WORKER_BATCH_SIZE", "5"))
HAIKU_TAG_VOCAB_SIZE: int = int(os.getenv("HAIKU_TAG_VOCAB_SIZE", "2000"))


def validate_write_path(path: Path) -> None:
    """Raise PermissionError if path is not under an allowed write location."""
    resolved = path.expanduser().resolve()
    for allowed in ALLOWED_WRITE_PATHS:
        try:
            resolved.relative_to(allowed.expanduser().resolve())
            return
        except ValueError:
            continue
        # exact match
    # Check if the path IS one of the allowed paths exactly
    for allowed in ALLOWED_WRITE_PATHS:
        if resolved == allowed.expanduser().resolve():
            return
    raise PermissionError(
        f"Write to '{resolved}' is not permitted. "
        f"Allowed paths: {[str(p) for p in ALLOWED_WRITE_PATHS]}"
    )


# ── API credentials (never log or print these values) ────────────────────────

def get_zotero_api_key() -> str | None:
    """Return ZOTERO_API_KEY from environment, or None if unset."""
    return os.getenv("ZOTERO_API_KEY") or None


def get_openai_api_key() -> str | None:
    """Return OPENAI_API_KEY from environment, or None if unset."""
    return os.getenv("OPENAI_API_KEY") or None


def require_zotero_api_key() -> str:
    """Return ZOTERO_API_KEY or exit with a clear error message."""
    key = get_zotero_api_key()
    if not key:
        raise EnvironmentError(
            "ZOTERO_API_KEY is required for Phase 4 (export/upload) and "
            "optional Phase 1 API fallback. "
            "Set it via 'export ZOTERO_API_KEY=<key>' or add it to .env."
        )
    return key


def require_openai_api_key() -> str:
    """Return OPENAI_API_KEY or exit with a clear error message."""
    key = get_openai_api_key()
    if not key:
        raise EnvironmentError(
            "OPENAI_API_KEY is required for Phase 3 semantic clustering. "
            "Set it via 'export OPENAI_API_KEY=<key>' or add it to .env."
        )
    return key


# ── Tag categorization taxonomy (editable without code changes) ───────────────
# Each key is a category name; the value is a list of keywords/phrases that
# trigger assignment to that category.  Matching is case-insensitive substring.

TAG_DOMAIN_KEYWORDS: dict[str, list[str]] = {
    "neuroscience": [
        "neuron", "neural", "synapse", "synaptic", "cortex", "cortical",
        "hippocampus", "cerebellum", "dendrite", "dendritic", "spike",
        "spiking", "axon", "membrane", "brain", "neuro", "cognit",
        "plasticity", "ltp", "ltd", "nmda", "ampa", "gaba", "dopamine",
        "serotonin", "working memory", "auditory", "sensory", "motor",
        "basal ganglia", "thalamus", "prefrontal", "electrophysiology",
        "patch clamp", "in vivo", "in vitro",
    ],
    "machine-learning": [
        "machine learning", "deep learning", "neural network", "transformer",
        "attention", "lstm", "rnn", "cnn", "autoencoder", "gan", "vae",
        "reinforcement learning", "supervised", "unsupervised", "gradient",
        "backprop", "classification", "regression", "clustering", "embedding",
        "bert", "gpt", "llm", "language model",
    ],
    "mathematics": [
        "differential equation", "stochastic", "dynamical system", "bifurcation",
        "topology", "algebra", "probability", "statistics", "bayesian",
        "information theory", "entropy", "markov", "matrix", "linear algebra",
    ],
    "linguistics": [
        "phoneme", "phonology", "word recognition", "speech", "language",
        "lexical", "phonological", "morphology", "syntax", "semantics",
        "prosody", "phonetic",
    ],
    "biology": [
        "gene", "protein", "cell", "molecular", "dna", "rna", "expression",
        "evolution", "genome", "organism", "tissue",
    ],
    "neuromorphic": [
        "neuromorphic", "hardware", "fpga", "chip", "loihi", "spinnaker",
        "analog circuit", "memristor",
    ],
}

TAG_METHOD_KEYWORDS: dict[str, list[str]] = {
    "simulation": ["simulation", "simulated", "model", "computational model", "snn"],
    "experimental": ["experiment", "recording", "measurement", "electrode", "imaging"],
    "theoretical": ["theory", "theoretical", "analytical", "proof", "derivation"],
    "review": ["review", "survey", "overview", "meta-analysis", "systematic"],
    "computational": ["computational", "algorithm", "optimization", "numerical"],
}

# Status tags: exact match (after lowercase normalization)
TAG_STATUS_VALUES: set[str] = {
    "unread",
    "to-read",
    "to read",
    "reading",
    "read",
    "done",
    "skimmed",
}

# Quality tags: exact match (after lowercase normalization)
TAG_QUALITY_VALUES: set[str] = {
    "important",
    "seminal",
    "foundational",
    "landmark",
    "weak",
    "retracted",
    "preliminary",
    "must-read",
    "must read",
    "highly cited",
}

# Tags that should never be auto-merged or normalized
TAG_PROTECTED: set[str] = {
    "⛔ no doifound",
    "⛔ no doi found",
    "#broken_attachments",
    "#duplicate_attachments",
    "#nosource",
    "❓ multiple doi",
}
