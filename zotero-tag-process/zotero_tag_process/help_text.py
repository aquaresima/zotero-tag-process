"""help_text.py — narrative help text for the unified CLI and web landing page."""

from __future__ import annotations

HELP_TEXT = """\
zotero-tag-process — Zotero tag enrichment pipeline
====================================================

WORKFLOW A — Generate tags with Ollama
---------------------------------------
Step 1:  zotero-tag-process ingest
         Reads your Zotero library and builds a working database.
         Output: ~/data/zotero_generatedtags_YYYY-MM-DD.sqlite

Step 2:  zotero-tag-process serve
         Opens the web UI. Go to "Tag Generation" -> page 1.
         Click "Generate Tags" to start Ollama. Tags appear in real time
         on page 2 as Ollama processes each batch.

Step 3:  In the web UI, review proposed tags per paper.
         Use the confidence slider to bulk-approve. Approved papers
         disappear from the queue.

WORKFLOW B — Apply approved tags to Zotero
-------------------------------------------
Step 4:  In the web UI, go to "Zotero Update" -> page 1.
         Select the generatedtags DB, click "Import & Normalize",
         then "Backup", then "Apply to Zotero".

         Or via CLI:
         zotero-tag-process import-tags --generated-db ~/data/zotero_generatedtags_DATE.sqlite

NOTES
-----
- taxonomy.yaml controls the prompt, method vocab, and field taxonomy.
  Edit it at ~/.claude/lib/zotero-restructuring/taxonomy.yaml
- Both tools can still be run standalone:
    cd ~/.claude/lib/tag-generation && uv run tag-generation serve
    cd ~/.claude/lib/zotero-restructuring && uv run zotero-restructuring serve
- The generatedtags DB is safe to regenerate — re-running ingest+generate
  skips papers that already have approved/rejected proposals.
"""
