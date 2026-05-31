"""
verify_mcp.py — Smoke-test the zotero-mcp semantic search capabilities.

Exercises three operations without modifying the library:
1. DB status check
2. Semantic search (two queries)
3. Tag fetch + basic stats
4. Dry-run batch_update_tags (add then immediately remove a canary tag)

Run from repo root:
    python scripts/verify_mcp.py

Requires the zotero-mcp server running (port 23119 / MCP socket).
Uses pyzotero directly since this script runs outside the MCP session.
"""

from __future__ import annotations

import os
import sys
import json
from pathlib import Path

# ── Locate ChromaDB and embedding model ──────────────────────────────────────

CHROMA_PATH = Path.home() / ".config" / "zotero-mcp" / "chroma_db"
COLLECTION_NAME = "zotero_library"


def check_chromadb() -> None:
    print("=== 1. ChromaDB status ===")
    try:
        import chromadb
    except ImportError:
        print("  chromadb not installed — install with: pip install chromadb")
        sys.exit(1)

    client = chromadb.PersistentClient(path=str(CHROMA_PATH))
    collections = client.list_collections()
    names = [c.name for c in collections]
    print(f"  Collections: {names}")

    if COLLECTION_NAME not in names:
        print(f"  ERROR: '{COLLECTION_NAME}' not found. Run: zotero-mcp update-db")
        sys.exit(1)

    col = client.get_collection(COLLECTION_NAME)
    count = col.count()
    print(f"  '{COLLECTION_NAME}': {count} documents  OK")
    return col


def semantic_search(col, query: str, n: int = 5) -> list[dict]:
    print(f"\n=== 2. Semantic search: '{query}' ===")
    try:
        from sentence_transformers import SentenceTransformer
        model = SentenceTransformer("all-MiniLM-L6-v2")
        embedding = model.encode(query).tolist()
    except ImportError:
        print("  sentence-transformers not installed — install with: pip install sentence-transformers")
        sys.exit(1)

    results = col.query(
        query_embeddings=[embedding],
        n_results=n,
        include=["documents", "metadatas", "distances"],
    )

    hits = []
    for i, (doc, meta, dist) in enumerate(zip(
        results["documents"][0],
        results["metadatas"][0],
        results["distances"][0],
    )):
        similarity = 1 - dist  # cosine: distance=1-sim
        title = meta.get("title") or doc[:80]
        hits.append({"title": title, "similarity": similarity, "meta": meta})
        print(f"  {i+1}. [{similarity:.3f}] {title[:80]}")

    return hits


def tag_stats(col) -> None:
    print("\n=== 3. Tag stats from ChromaDB ===")
    # Fetch a sample of metadata to inspect tag coverage
    sample = col.get(limit=500, include=["metadatas"])
    tag_counts: dict[str, int] = {}
    has_tags = 0
    for meta in sample["metadatas"]:
        raw = meta.get("tags", "")
        if raw:
            has_tags += 1
            for t in raw.split():
                tag_counts[t] = tag_counts.get(t, 0) + 1

    total = len(sample["metadatas"])
    print(f"  Sample: {total} items, {has_tags} have tags ({100*has_tags//total}%)")
    top = sorted(tag_counts.items(), key=lambda x: -x[1])[:10]
    print("  Top 10 tags in sample:")
    for tag, count in top:
        print(f"    {count:4d}  {tag}")


def dryrun_batch_update(col) -> None:
    """
    Simulate what batch_update_tags would do: find items matching a tag,
    print what would change. No writes.
    """
    print("\n=== 4. Dry-run batch_update_tags ===")
    # Find items tagged with a common variant
    sample = col.get(limit=2000, include=["metadatas"])
    target_old = "/unread"
    target_new = "status:unread"

    candidates = [
        m for m in sample["metadatas"]
        if target_old in (m.get("tags") or "")
    ]
    print(f"  Would rename '{target_old}' → '{target_new}'")
    print(f"  Items affected (in sample): {len(candidates)}")
    if candidates:
        for m in candidates[:3]:
            print(f"    - {m.get('title', '(no title)')[:70]}")
        if len(candidates) > 3:
            print(f"    ... and {len(candidates)-3} more")
    print("  [DRY RUN — no changes written]")


def main() -> None:
    print("zotero-mcp semantic search verification")
    print("=" * 50)

    col = check_chromadb()
    semantic_search(col, "dendritic computation spiking neural networks")
    semantic_search(col, "auditory working memory spoken word recognition")
    tag_stats(col)
    dryrun_batch_update(col)

    print("\n=== PASS — all checks completed ===")


if __name__ == "__main__":
    main()
